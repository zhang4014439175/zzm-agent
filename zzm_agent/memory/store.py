from __future__ import annotations

from pathlib import Path

from zzm_agent.memory.episodic_store import EpisodicStore
from zzm_agent.memory.history_store import HistoryStore
from zzm_agent.memory.io import StorageIO
from zzm_agent.memory.semantic_store import SemanticStore
from zzm_agent.memory.session_store import SessionStore


class MemoryStore:
    """Compose session, history, episodic, and semantic memory storage."""

    def __init__(
        self,
        path: str | Path,
        max_history: int = 50,
        session_id: str | None = None,
        retrieval_top_k: int = 3,
    ):
        self.max_history = max_history
        self.retrieval_top_k = retrieval_top_k

        self.io = StorageIO()
        self.sessions = SessionStore(self.io, path=path)
        self.history_store = HistoryStore(
            self.io,
            self.sessions,
            max_history=max_history,
        )
        self.episodic_store = EpisodicStore(self.io, self.sessions)
        self.semantic_store = SemanticStore(self.io, self.sessions.base_dir)
        self.sessions.initialize(session_id=session_id)

    @property
    def legacy_path(self) -> Path:
        """Expose the legacy memory path for compatibility with existing tests."""
        return self.sessions.legacy_path

    @property
    def base_dir(self) -> Path:
        """Return the root directory that contains all memory files."""
        return self.sessions.base_dir

    @property
    def sessions_dir(self) -> Path:
        """Return the directory that stores per-session files."""
        return self.sessions.sessions_dir

    @property
    def index_path(self) -> Path:
        """Return the path of the session index file."""
        return self.sessions.index_path

    @property
    def last_session_path(self) -> Path:
        """Return the path of the file that tracks the last active session."""
        return self.sessions.last_session_path

    @property
    def session_id(self) -> str:
        """Return the id of the currently active session."""
        return self.sessions.session_id

    @property
    def history_path(self) -> Path:
        """Return the path of the active session's history file."""
        return self.sessions.history_path()

    @property
    def meta_path(self) -> Path:
        """Return the path of the active session's metadata file."""
        return self.sessions.meta_path()

    @property
    def semantic_path(self) -> Path:
        """Return the path of the shared semantic memory file."""
        return self.semantic_store.semantic_path

    @property
    def episodic_path(self) -> Path:
        """Return the path of the active session's episodic summary file."""
        return self.sessions.episodic_path()

    def load_history(self) -> list[dict]:
        """Load the recent transcript for the active session."""
        return self.history_store.load_history()

    def append(self, messages: list[dict]) -> None:
        """Append messages to the active session and refresh episodic memory."""
        history = self.history_store.append(messages)
        self.episodic_store.update(self.session_id, history=history)

    def list_sessions(self) -> list[dict]:
        """Return every known session ordered by most recent activity."""
        return self.sessions.list_sessions()

    def get_current_session(self) -> dict:
        """Return metadata for the currently active session."""
        return self.sessions.get_current_session()

    def get_session(self, session_id: str) -> dict:
        """Look up one session's metadata from the session index."""
        return self.sessions.get_session(session_id)

    def ensure_session(self, session_id: str, name: str | None = None) -> dict:
        """Create the on-disk structure for one session if it does not exist."""
        return self.sessions.ensure_session(session_id, name=name)

    def create_session(self, name: str | None = None, make_current: bool = True) -> dict:
        """Create a new session and optionally make it the active one."""
        session_id_before = self.session_id
        if session_id_before and make_current:
            # Persist a session-level summary at the switch boundary so the next
            # session can reference what was concluded here.
            self.episodic_store.update(session_id_before)
        return self.sessions.create_session(name=name, make_current=make_current)

    def switch_session(self, session_id: str) -> dict:
        """Activate one session and persist it as the default for next startup."""
        if self.session_id and self.session_id != session_id:
            # Persist a session-level summary at the switch boundary so the next
            # session can reference what was concluded here.
            self.episodic_store.update(self.session_id)
        return self.sessions.switch_session(session_id)

    def load_episodic(self, session_id: str | None = None) -> dict | None:
        """Load a persisted episodic summary for one session when available."""
        return self.episodic_store.load(session_id=session_id)

    def list_episodic(self, exclude_session_id: str | None = None) -> list[dict]:
        """List episodic summaries ordered by recency across sessions."""
        return self.episodic_store.list(exclude_session_id=exclude_session_id)

    def load_semantic_memory(self) -> list[dict]:
        """Load cross-session semantic memory entries ordered by recency."""
        return self.semantic_store.load()

    def remember_fact(self, fact: str) -> dict:
        """Insert or refresh one semantic memory fact."""
        return self.semantic_store.remember(fact, now=self.sessions.utc_now())

    def forget_fact(self, keyword: str) -> int:
        """Remove semantic memory entries whose text matches the keyword."""
        return self.semantic_store.forget(keyword)

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
