from __future__ import annotations

from zzm_agent.memory.io import StorageIO
from zzm_agent.memory.session_store import SessionStore


class EpisodicStore:
    """Persist session-level summaries used for cross-session recall."""

    def __init__(self, io: StorageIO, sessions: SessionStore):
        self.io = io
        self.sessions = sessions

    def load(self, session_id: str | None = None) -> dict | None:
        """Load a persisted episodic summary for one session when available."""
        path = self.sessions.episodic_path(session_id)
        data = self.io.read_json(path, default=None)
        if isinstance(data, dict) and data.get("summary"):
            return data
        return None

    def list(self, exclude_session_id: str | None = None) -> list[dict]:
        """List episodic summaries ordered by recency across sessions."""
        summaries: list[dict] = []
        for session in self.sessions.list_sessions():
            session_id = session["id"]
            if exclude_session_id and session_id == exclude_session_id:
                continue
            summary = self.load(session_id)
            if summary:
                summaries.append(summary)
        return sorted(
            summaries,
            key=lambda entry: entry.get("updated_at", ""),
            reverse=True,
        )

    def update(self, session_id: str, history: list[dict] | None = None) -> None:
        """Persist the latest episodic summary for one session."""
        if not session_id:
            return

        if history is None:
            history = self.io.read_json(self.sessions.history_path(session_id), default=[])
        if not isinstance(history, list) or not history:
            return

        # Episodic memory is intentionally a lightweight extract of the recent
        # user/assistant exchange, not a second full transcript.
        summary = self._build_summary(history)
        if not summary:
            return

        entry = {
            "session_id": session_id,
            "summary": summary,
            "updated_at": self.sessions.utc_now(),
        }
        self.io.write_json(self.sessions.episodic_path(session_id), entry)

    def _build_summary(self, history: list[dict]) -> str:
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
