from __future__ import annotations

from zzm_agent.memory.io import StorageIO
from zzm_agent.memory.session_store import SessionStore


class HistoryStore:
    """Persist working-memory history inside the active session directory."""

    def __init__(self, io: StorageIO, sessions: SessionStore, max_history: int):
        self.io = io
        self.sessions = sessions
        self.max_history = max_history

    def load_history(self, session_id: str | None = None) -> list[dict]:
        """Load the recent transcript for one session."""
        history_path = self.sessions.history_path(session_id)
        data = self.io.read_json(history_path, default=[])
        if not isinstance(data, list):
            return []
        return data[-self.max_history :]

    def append(self, messages: list[dict], session_id: str | None = None) -> list[dict]:
        """Append messages to one session transcript and return the full history."""
        target_session = session_id or self.sessions.session_id
        history_path = self.sessions.history_path(target_session)
        existing = self.io.read_json(history_path, default=[])
        if not isinstance(existing, list):
            existing = []
        existing.extend(messages)
        self.io.write_json(history_path, existing)
        self.sessions.touch_session(target_session)
        return existing
