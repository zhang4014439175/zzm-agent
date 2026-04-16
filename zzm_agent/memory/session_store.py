from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from zzm_agent.memory.io import StorageIO


def utc_now() -> str:
    """Return an ISO timestamp in UTC."""
    return datetime.now(timezone.utc).isoformat()


class SessionStore:
    """Manage session directories, metadata, migration, and startup recovery."""

    def __init__(self, io: StorageIO, path: str | Path):
        self.io = io
        self.legacy_path = Path(path).expanduser().resolve()
        self.base_dir = self.legacy_path.parent
        self.sessions_dir = self.base_dir / "sessions"
        self.index_path = self.sessions_dir / "index.json"
        self.last_session_path = self.sessions_dir / "last_session.txt"
        self.session_id = ""

        self.base_dir.mkdir(parents=True, exist_ok=True)
        self.sessions_dir.mkdir(parents=True, exist_ok=True)
        self.cleanup_partial_state()

    def history_path(self, session_id: str | None = None) -> Path:
        """Return the path of one session's history file."""
        return self.session_dir(session_id or self.session_id) / "history.json"

    def meta_path(self, session_id: str | None = None) -> Path:
        """Return the path of one session's metadata file."""
        return self.session_dir(session_id or self.session_id) / "meta.json"

    def episodic_path(self, session_id: str | None = None) -> Path:
        """Return the path of one session's episodic summary file."""
        return self.session_dir(session_id or self.session_id) / "episodic.json"

    def initialize(self, session_id: str | None = None) -> None:
        """Finish startup by migrating legacy data and selecting an active session."""
        # Legacy migration must happen before any session is selected so the
        # first post-upgrade startup can immediately resume migrated history.
        self.migrate_legacy_memory()

        if session_id:
            # An explicit CLI/session selection takes precedence over the last
            # remembered session and creates the target on demand.
            self.ensure_session(session_id)
            self.switch_session(session_id)
            return

        last_session = self.read_last_session()
        if last_session and self.session_dir(last_session).exists():
            # Default startup resumes the most recently active session so the
            # agent continues from the same conversation boundary.
            self.switch_session(last_session)
            return

        sessions = self.list_sessions()
        if sessions:
            self.switch_session(sessions[0]["id"])
            return

        self.create_session(make_current=True)

    def utc_now(self) -> str:
        """Return the current UTC timestamp for session metadata updates."""
        return utc_now()

    def list_sessions(self) -> list[dict]:
        """Return every known session ordered by most recent activity."""
        index = self.io.read_json(self.index_path, default=[])
        if not isinstance(index, list):
            return []
        return sorted(
            [entry for entry in index if isinstance(entry, dict) and entry.get("id")],
            key=lambda entry: entry.get("updated_at", ""),
            reverse=True,
        )

    def get_current_session(self) -> dict:
        """Return metadata for the currently active session."""
        return self.get_session(self.session_id)

    def get_session(self, session_id: str) -> dict:
        """Look up one session's metadata from the session index."""
        for entry in self.list_sessions():
            if entry["id"] == session_id:
                return entry
        raise KeyError(f"Session not found: {session_id}")

    def ensure_session(self, session_id: str, name: str | None = None) -> dict:
        """Create the on-disk structure for one session if it does not exist."""
        normalized = self.normalize_session_id(session_id)
        session_dir = self.session_dir(normalized)
        session_dir.mkdir(parents=True, exist_ok=True)

        if not self.history_path(normalized).exists():
            self.io.write_json(self.history_path(normalized), [])

        meta = self.load_meta(normalized)
        if meta is None:
            now = utc_now()
            meta = {
                "id": normalized,
                "name": name or normalized,
                "created_at": now,
                "updated_at": now,
            }
            self.io.write_json(self.meta_path(normalized), meta)

        self.upsert_index(meta)
        return meta

    def create_session(self, name: str | None = None, make_current: bool = True) -> dict:
        """Create a new session and optionally make it the active one."""
        session_id = self.generate_session_id()
        meta = self.ensure_session(session_id, name=name or session_id)
        if make_current:
            self.switch_session(session_id)
        return meta

    def switch_session(self, session_id: str) -> dict:
        """Activate one session and persist it as the default for next startup."""
        meta = self.ensure_session(session_id)
        self.session_id = meta["id"]
        self.io.write_text(self.last_session_path, self.session_id)
        return meta

    def touch_session(self, session_id: str) -> None:
        """Refresh one session's metadata after new history is appended."""
        meta = self.ensure_session(session_id)
        meta["updated_at"] = utc_now()
        self.io.write_json(self.meta_path(session_id), meta)
        self.upsert_index(meta)
        self.io.write_text(self.last_session_path, session_id)

    def load_meta(self, session_id: str) -> dict | None:
        """Load one session's metadata file when it is present and valid."""
        data = self.io.read_json(self.meta_path(session_id), default=None)
        if isinstance(data, dict) and data.get("id"):
            return data
        return None

    def upsert_index(self, meta: dict) -> None:
        """Insert or replace one session record in the global session index."""
        index = self.io.read_json(self.index_path, default=[])
        if not isinstance(index, list):
            index = []

        updated = False
        new_index = []
        for entry in index:
            if not isinstance(entry, dict) or not entry.get("id"):
                continue
            if entry["id"] == meta["id"]:
                new_index.append(meta)
                updated = True
            else:
                new_index.append(entry)

        if not updated:
            new_index.append(meta)

        self.io.write_json(self.index_path, new_index)

    def read_last_session(self) -> str | None:
        """Read the session id that should be resumed on the next startup."""
        if not self.last_session_path.exists():
            return None
        try:
            value = self.last_session_path.read_text(encoding="utf-8").strip()
        except OSError:
            return None
        return value or None

    def generate_session_id(self, prefix: str = "session") -> str:
        """Generate a unique session id under the current sessions directory."""
        while True:
            session_id = f"{prefix}-{uuid4().hex[:8]}"
            if not self.session_dir(session_id).exists():
                return session_id

    def normalize_session_id(self, session_id: str) -> str:
        """Normalize user-provided session ids before using them on disk."""
        normalized = session_id.strip()
        if not normalized:
            raise ValueError("Session id cannot be empty.")
        return normalized.replace(" ", "-")

    def session_dir(self, session_id: str) -> Path:
        """Return the directory path used to store one session's files."""
        return self.sessions_dir / session_id

    def migrate_legacy_memory(self) -> None:
        """Migrate legacy memory.json into the session layout once."""
        # Migration is single-shot: once any session structure exists we stop
        # importing `memory.json` to avoid duplicate histories on restart.
        if self.list_sessions() or any(self.sessions_dir.iterdir()):
            return
        if not self.legacy_path.exists():
            return

        legacy_history = self.io.read_json(self.legacy_path, default=[])
        if not isinstance(legacy_history, list):
            return

        session_id = self.generate_session_id(prefix="migrated")
        session_dir = self.session_dir(session_id)
        session_dir.mkdir(parents=True, exist_ok=True)

        now = utc_now()
        meta = {
            "id": session_id,
            "name": "Migrated Session",
            "created_at": now,
            "updated_at": now,
            "source": str(self.legacy_path),
        }
        index_existed = self.index_path.exists()
        last_session_existed = self.last_session_path.exists()
        previous_index = self.io.read_bytes(self.index_path)
        previous_last_session = self.io.read_bytes(self.last_session_path)

        try:
            # Write the migrated session atomically enough that a successful run
            # leaves a resumable session and marks it as the last active one.
            self.io.write_json(self.history_path(session_id), legacy_history)
            self.io.write_json(self.meta_path(session_id), meta)
            self.io.write_json(self.index_path, [meta])
            self.io.write_text(self.last_session_path, session_id)
        except OSError:
            self.io.restore_file(
                self.index_path,
                previous_index,
                existed=index_existed,
            )
            self.io.restore_file(
                self.last_session_path,
                previous_last_session,
                existed=last_session_existed,
            )
            self.io.remove_tree(session_dir)
            self.cleanup_partial_state()
            raise

    def cleanup_partial_state(self) -> None:
        """Remove incomplete migration/session artifacts before startup continues."""
        for tmp_path in self.sessions_dir.rglob("*.tmp"):
            try:
                tmp_path.unlink(missing_ok=True)
            except OSError:
                continue

        for session_dir in self.sessions_dir.iterdir():
            if not session_dir.is_dir():
                continue
            has_history = self.history_path(session_dir.name).exists()
            has_meta = self.meta_path(session_dir.name).exists()
            if has_history and has_meta:
                continue
            try:
                self.io.remove_tree(session_dir)
            except OSError:
                continue

        for path in (
            self.index_path.with_suffix(self.index_path.suffix + ".tmp"),
            self.last_session_path.with_suffix(self.last_session_path.suffix + ".tmp"),
        ):
            try:
                path.unlink(missing_ok=True)
            except OSError:
                continue
