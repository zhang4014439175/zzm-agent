from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class MemoryStore:
    """
    Persist conversation history in session-scoped files under ``sessions/``.

    The legacy ``memory.json`` path is still accepted as the storage root input.
    On first startup the file is migrated into the new session layout.
    """

    def __init__(
        self,
        path: str | Path,
        max_history: int = 50,
        session_id: str | None = None,
        retrieval_top_k: int = 3,
    ):
        self.legacy_path = Path(path).expanduser().resolve()
        self.base_dir = self.legacy_path.parent
        self.max_history = max_history
        self.retrieval_top_k = retrieval_top_k

        self.sessions_dir = self.base_dir / "sessions"
        self.index_path = self.sessions_dir / "index.json"
        self.last_session_path = self.sessions_dir / "last_session.txt"
        # Semantic memory is shared across sessions, so it lives beside the
        # session tree rather than inside any single session directory.
        self.semantic_path = self.base_dir / "semantic.json"
        self.session_id = ""

        self.base_dir.mkdir(parents=True, exist_ok=True)
        self.sessions_dir.mkdir(parents=True, exist_ok=True)
        self._cleanup_partial_state()

        # Legacy migration must happen before any session is selected so the
        # first post-upgrade startup can immediately resume migrated history.
        self._migrate_legacy_memory()

        if session_id:
            # An explicit CLI/session selection takes precedence over the last
            # remembered session and creates the target on demand.
            self.ensure_session(session_id)
            self.switch_session(session_id)
            return

        last_session = self._read_last_session()
        if last_session and self._session_dir(last_session).exists():
            # Default startup resumes the most recently active session so the
            # agent continues from the same conversation boundary.
            self.switch_session(last_session)
            return

        sessions = self.list_sessions()
        if sessions:
            self.switch_session(sessions[0]["id"])
            return

        self.create_session(make_current=True)

    @property
    def history_path(self) -> Path:
        return self._session_dir(self.session_id) / "history.json"

    @property
    def meta_path(self) -> Path:
        return self._session_dir(self.session_id) / "meta.json"

    @property
    def episodic_path(self) -> Path:
        """Return the path of the active session's episodic summary file."""
        return self._session_dir(self.session_id) / "episodic.json"

    def load_history(self) -> list[dict]:
        data = self._read_json(self.history_path, default=[])
        if not isinstance(data, list):
            return []
        return data[-self.max_history :]

    def append(self, messages: list[dict]) -> None:
        existing = self._read_json(self.history_path, default=[])
        if not isinstance(existing, list):
            existing = []
        existing.extend(messages)
        self._write_json(self.history_path, existing)
        self._update_episodic_summary(self.session_id, history=existing)
        self._touch_session(self.session_id)

    def list_sessions(self) -> list[dict]:
        """Return every known session ordered by most recent activity."""
        index = self._read_json(self.index_path, default=[])
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
        normalized = self._normalize_session_id(session_id)
        session_dir = self._session_dir(normalized)
        session_dir.mkdir(parents=True, exist_ok=True)

        if not (session_dir / "history.json").exists():
            self._write_json(session_dir / "history.json", [])

        meta = self._load_meta(normalized)
        if meta is None:
            now = _utc_now()
            meta = {
                "id": normalized,
                "name": name or normalized,
                "created_at": now,
                "updated_at": now,
            }
            self._write_json(session_dir / "meta.json", meta)

        self._upsert_index(meta)
        return meta

    def create_session(self, name: str | None = None, make_current: bool = True) -> dict:
        """Create a new session and optionally make it the active one."""
        session_id = self._generate_session_id()
        meta = self.ensure_session(session_id, name=name or session_id)
        if make_current:
            self.switch_session(session_id)
        return meta

    def switch_session(self, session_id: str) -> dict:
        """Activate one session and persist it as the default for next startup."""
        # Switching updates in-memory state plus `last_session.txt`, making the
        # new session the default target for subsequent restarts.
        if self.session_id and self.session_id != session_id:
            # Persist a session-level summary at the switch boundary so the next
            # session can reference what was concluded here.
            self._update_episodic_summary(self.session_id)
        meta = self.ensure_session(session_id)
        self.session_id = meta["id"]
        self._write_text(self.last_session_path, self.session_id)
        return meta

    def load_episodic(self, session_id: str | None = None) -> dict | None:
        """Load a persisted episodic summary for one session when available."""
        target_session = session_id or self.session_id
        path = self._session_dir(target_session) / "episodic.json"
        data = self._read_json(path, default=None)
        if isinstance(data, dict) and data.get("summary"):
            return data
        return None

    def list_episodic(self, exclude_session_id: str | None = None) -> list[dict]:
        """List episodic summaries ordered by recency across sessions."""
        summaries: list[dict] = []
        for session in self.list_sessions():
            session_id = session["id"]
            if exclude_session_id and session_id == exclude_session_id:
                continue
            summary = self.load_episodic(session_id)
            if summary:
                summaries.append(summary)
        return sorted(
            summaries,
            key=lambda entry: entry.get("updated_at", ""),
            reverse=True,
        )

    def load_semantic_memory(self) -> list[dict]:
        """Load cross-session semantic memory entries ordered by recency."""
        data = self._read_json(self.semantic_path, default=[])
        if not isinstance(data, list):
            return []
        entries = [
            entry for entry in data if isinstance(entry, dict) and entry.get("fact")
        ]
        return sorted(
            entries,
            key=lambda entry: entry.get("updated_at", ""),
            reverse=True,
        )

    def remember_fact(self, fact: str) -> dict:
        """Insert or refresh one semantic memory fact."""
        cleaned = fact.strip()
        if not cleaned:
            raise ValueError("Fact cannot be empty.")

        normalized = self._normalize_memory_text(cleaned)
        entries = self.load_semantic_memory()
        now = _utc_now()
        for entry in entries:
            # Re-remembering the same fact refreshes it instead of duplicating
            # semantic memory injections across future sessions.
            if entry.get("normalized_fact") == normalized:
                entry["fact"] = cleaned
                entry["updated_at"] = now
                self._write_json(self.semantic_path, entries)
                return entry

        entry = {
            "fact": cleaned,
            "normalized_fact": normalized,
            "created_at": now,
            "updated_at": now,
        }
        entries.append(entry)
        self._write_json(self.semantic_path, entries)
        return entry

    def forget_fact(self, keyword: str) -> int:
        """Remove semantic memory entries whose text matches the keyword."""
        normalized = self._normalize_memory_text(keyword)
        if not normalized:
            raise ValueError("Keyword cannot be empty.")

        existing = self.load_semantic_memory()
        retained = [
            entry
            for entry in existing
            if normalized not in self._normalize_memory_text(entry.get("fact", ""))
        ]
        removed = len(existing) - len(retained)
        if removed:
            self._write_json(self.semantic_path, retained)
        return removed

    def build_memory_messages(self, limit: int | None = None) -> list[dict[str, str]]:
        """Build system messages used to inject long-term memory into a turn."""
        max_items = limit if limit is not None else self.retrieval_top_k
        if max_items <= 0:
            return []

        # Memory injection is bounded so retrieval cannot silently overwhelm the
        # current conversation context.
        semantic_entries = self.load_semantic_memory()[:max_items]
        episodic_entries = self.list_episodic(exclude_session_id=self.session_id)[:max_items]

        semantic_lines = [entry["fact"] for entry in semantic_entries if entry.get("fact")]
        episodic_lines = []
        for entry in episodic_entries:
            session_id = entry.get("session_id", "unknown-session")
            summary = entry.get("summary", "").strip()
            if summary:
                episodic_lines.append(f"{session_id}: {summary}")

        messages: list[dict[str, str]] = []
        if semantic_lines:
            messages.append(
                {
                    "role": "system",
                    "content": "Semantic memory:\n- " + "\n- ".join(semantic_lines),
                }
            )
        if episodic_lines:
            messages.append(
                {
                    "role": "system",
                    "content": "Episodic memory:\n- " + "\n- ".join(episodic_lines),
                }
            )
        return messages

    def _migrate_legacy_memory(self) -> None:
        # Migration is single-shot: once any session structure exists we stop
        # importing `memory.json` to avoid duplicate histories on restart.
        if self.list_sessions() or any(self.sessions_dir.iterdir()):
            return
        if not self.legacy_path.exists():
            return

        legacy_history = self._read_json(self.legacy_path, default=[])
        if not isinstance(legacy_history, list):
            return

        session_id = self._generate_session_id(prefix="migrated")
        session_dir = self._session_dir(session_id)
        session_dir.mkdir(parents=True, exist_ok=True)

        now = _utc_now()
        meta = {
            "id": session_id,
            "name": "Migrated Session",
            "created_at": now,
            "updated_at": now,
            "source": str(self.legacy_path),
        }
        index_existed = self.index_path.exists()
        last_session_existed = self.last_session_path.exists()
        previous_index = self._read_bytes(self.index_path)
        previous_last_session = self._read_bytes(self.last_session_path)

        try:
            # Write the migrated session atomically enough that a successful run
            # leaves a resumable session and marks it as the last active one.
            self._write_json(session_dir / "history.json", legacy_history)
            self._write_json(session_dir / "meta.json", meta)
            self._write_json(self.index_path, [meta])
            self._write_text(self.last_session_path, session_id)
        except OSError:
            self._restore_file(
                self.index_path,
                previous_index,
                existed=index_existed,
            )
            self._restore_file(
                self.last_session_path,
                previous_last_session,
                existed=last_session_existed,
            )
            self._remove_tree(session_dir)
            self._cleanup_partial_state()
            raise

    def _touch_session(self, session_id: str) -> None:
        """Refresh one session's metadata after new history is appended."""
        meta = self.ensure_session(session_id)
        meta["updated_at"] = _utc_now()
        self._write_json(self._session_dir(session_id) / "meta.json", meta)
        self._upsert_index(meta)
        self._write_text(self.last_session_path, session_id)

    def _load_meta(self, session_id: str) -> dict | None:
        """Load one session's metadata file when it is present and valid."""
        path = self._session_dir(session_id) / "meta.json"
        data = self._read_json(path, default=None)
        if isinstance(data, dict) and data.get("id"):
            return data
        return None

    def _upsert_index(self, meta: dict) -> None:
        """Insert or replace one session record in the global session index."""
        index = self._read_json(self.index_path, default=[])
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

        self._write_json(self.index_path, new_index)

    def _read_last_session(self) -> str | None:
        """Read the session id that should be resumed on the next startup."""
        if not self.last_session_path.exists():
            return None
        try:
            value = self.last_session_path.read_text(encoding="utf-8").strip()
        except OSError:
            return None
        return value or None

    def _generate_session_id(self, prefix: str = "session") -> str:
        """Generate a unique session id under the current sessions directory."""
        while True:
            session_id = f"{prefix}-{uuid4().hex[:8]}"
            if not self._session_dir(session_id).exists():
                return session_id

    def _normalize_session_id(self, session_id: str) -> str:
        """Normalize user-provided session ids before using them on disk."""
        normalized = session_id.strip()
        if not normalized:
            raise ValueError("Session id cannot be empty.")
        return normalized.replace(" ", "-")

    def _session_dir(self, session_id: str) -> Path:
        """Return the directory path used to store one session's files."""
        return self.sessions_dir / session_id

    def _read_json(self, path: Path, default):
        if not path.exists():
            return default
        try:
            with path.open("r", encoding="utf-8") as handle:
                return json.load(handle)
        except (json.JSONDecodeError, OSError):
            return default

    def _write_json(self, path: Path, data) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = path.with_suffix(path.suffix + ".tmp")
        with tmp_path.open("w", encoding="utf-8") as handle:
            json.dump(data, handle, ensure_ascii=False, indent=2)
        tmp_path.replace(path)

    def _write_text(self, path: Path, value: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = path.with_suffix(path.suffix + ".tmp")
        with tmp_path.open("w", encoding="utf-8") as handle:
            handle.write(value)
        tmp_path.replace(path)

    def _read_bytes(self, path: Path) -> bytes | None:
        if not path.exists():
            return None
        try:
            return path.read_bytes()
        except OSError:
            return None

    def _restore_file(self, path: Path, content: bytes | None, existed: bool) -> None:
        if existed and content is not None:
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp_path = path.with_suffix(path.suffix + ".tmp")
            with tmp_path.open("wb") as handle:
                handle.write(content)
            tmp_path.replace(path)
            return

        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass

    def _remove_tree(self, path: Path) -> None:
        if not path.exists():
            return
        for child in path.iterdir():
            if child.is_dir():
                self._remove_tree(child)
            else:
                child.unlink(missing_ok=True)
        path.rmdir()

    def _cleanup_partial_state(self) -> None:
        for tmp_path in self.sessions_dir.rglob("*.tmp"):
            try:
                tmp_path.unlink(missing_ok=True)
            except OSError:
                continue

        for session_dir in self.sessions_dir.iterdir():
            if not session_dir.is_dir():
                continue
            has_history = (session_dir / "history.json").exists()
            has_meta = (session_dir / "meta.json").exists()
            if has_history and has_meta:
                continue
            try:
                self._remove_tree(session_dir)
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

    def _update_episodic_summary(
        self,
        session_id: str,
        history: list[dict] | None = None,
    ) -> None:
        """Persist the latest episodic summary for one session."""
        if not session_id:
            return

        if history is None:
            history_path = self._session_dir(session_id) / "history.json"
            history = self._read_json(history_path, default=[])
        if not isinstance(history, list) or not history:
            return

        # Episodic memory is intentionally a lightweight extract of the recent
        # user/assistant exchange, not a second full transcript.
        summary = self._build_episodic_summary(history)
        if not summary:
            return

        entry = {
            "session_id": session_id,
            "summary": summary,
            "updated_at": _utc_now(),
        }
        self._write_json(self._session_dir(session_id) / "episodic.json", entry)

    def _build_episodic_summary(self, history: list[dict]) -> str:
        """Create a short session-level summary from recent dialogue turns."""
        excerpts: list[str] = []
        for message in history:
            role = message.get("role")
            if role not in {"user", "assistant"}:
                continue
            content = self._message_excerpt(message.get("content", ""))
            if not content:
                continue
            prefix = "User" if role == "user" else "Assistant"
            excerpts.append(f"{prefix}: {content}")

        if not excerpts:
            return ""
        return " | ".join(excerpts[-4:])

    def _message_excerpt(self, content: object, limit: int = 160) -> str:
        """Collapse whitespace and trim message content for summary storage."""
        text = " ".join(str(content).split())
        if len(text) <= limit:
            return text
        return text[: limit - 3] + "..."

    def _normalize_memory_text(self, text: str) -> str:
        """Normalize memory text for de-duplication and keyword matching."""
        return " ".join(text.lower().split())
