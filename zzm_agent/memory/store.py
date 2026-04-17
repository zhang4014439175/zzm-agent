from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from zzm_agent.memory.episodic_store import EpisodicStore
from zzm_agent.memory.history_store import HistoryStore
from zzm_agent.memory.io import StorageIO
from zzm_agent.memory.retriever import KeywordMemoryRetriever, MemoryRetriever
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
        max_context_tokens: int = 8000,
        compression_keep_recent: int = 10,
    ):
        self.max_history = max_history
        self.retrieval_top_k = retrieval_top_k
        self.max_context_tokens = max_context_tokens
        self.compression_keep_recent = compression_keep_recent

        self.io = StorageIO()
        self.sessions = SessionStore(self.io, path=path)
        self.history_store = HistoryStore(
            self.io,
            self.sessions,
            max_history=max_history,
        )
        self.episodic_store = EpisodicStore(self.io, self.sessions)
        self.semantic_store = SemanticStore(self.io, self.sessions.base_dir)
        self.retriever: MemoryRetriever = KeywordMemoryRetriever()
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

    def list_semantic_facts(self) -> list[str]:
        """Return every long-term semantic memory fact ordered by recency."""
        return self.semantic_store.list_facts()

    def remember_fact(self, fact: str) -> dict:
        """Insert or refresh one semantic memory fact."""
        return self.semantic_store.remember(fact, now=self.sessions.utc_now())

    def forget_fact(self, keyword: str) -> int:
        """Remove semantic memory entries whose text matches the keyword."""
        return self.semantic_store.forget(keyword)

    def search_memories(self, keyword: str, limit: int | None = None) -> dict[str, list[dict]]:
        """Search semantic and episodic memory entries related to one keyword."""
        max_items = limit if limit is not None else self.retrieval_top_k
        if max_items <= 0:
            return {"semantic": [], "episodic": []}

        return self.retriever.search(
            query=keyword,
            semantic_entries=self.load_semantic_memory(),
            episodic_entries=self.list_episodic(exclude_session_id=self.session_id),
            limit=max_items,
        )

    def build_memory_messages(
        self,
        query: str | None = None,
        limit: int | None = None,
    ) -> list[dict[str, str]]:
        """Build system messages used to inject long-term memory into a turn."""
        max_items = limit if limit is not None else self.retrieval_top_k
        if max_items <= 0:
            return []

        # Memory injection is bounded so retrieval cannot silently overwhelm the
        # current conversation context.
        if query is not None:
            retrieved = self.search_memories(query, limit=max_items)
            semantic_entries = retrieved["semantic"]
            episodic_entries = retrieved["episodic"]
        else:
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

    def build_turn_messages(
        self,
        system_prompt: str,
        user_input: str,
        memory_limit: int | None = None,
    ) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        """Assemble one model turn, compressing older history when needed."""
        history = self.load_history()
        messages: list[dict[str, Any]] = [{"role": "system", "content": system_prompt}]
        messages.extend(self.build_memory_messages(query=user_input, limit=memory_limit))

        reserved_messages = messages + [{"role": "user", "content": user_input}]
        reserved_tokens = self.estimate_messages_tokens(reserved_messages)
        history_budget = max(self.max_context_tokens - reserved_tokens, 0)
        compression = self.compress_history(history=history, budget_tokens=history_budget)
        messages.extend(compression["messages"])
        messages.append({"role": "user", "content": user_input})

        return messages, {
            **compression,
            "reserved_tokens": reserved_tokens,
            "max_context_tokens": self.max_context_tokens,
            "total_tokens": self.estimate_messages_tokens(messages),
        }

    def preview_context_window(self) -> dict[str, Any]:
        """Return a history-only preview of runtime compression for `/memory`."""
        return self.compress_history(
            history=self.load_history(),
            budget_tokens=self.max_context_tokens,
        )

    def compress_history(
        self,
        history: list[dict] | None = None,
        budget_tokens: int | None = None,
    ) -> dict[str, Any]:
        """Compress older history into one runtime-only summary when needed."""
        current_history = list(history if history is not None else self.load_history())
        token_budget = self.max_context_tokens if budget_tokens is None else budget_tokens
        raw_tokens = self.estimate_messages_tokens(current_history)

        if not current_history:
            return {
                "messages": [],
                "applied": False,
                "summary": "",
                "raw_count": 0,
                "compressed_count": 0,
                "kept_recent_count": 0,
                "dropped_count": 0,
                "raw_tokens": 0,
                "compressed_tokens": 0,
                "budget_tokens": token_budget,
            }

        if token_budget > 0 and raw_tokens <= token_budget:
            return {
                "messages": current_history,
                "applied": False,
                "summary": "",
                "raw_count": len(current_history),
                "compressed_count": len(current_history),
                "kept_recent_count": len(current_history),
                "dropped_count": 0,
                "raw_tokens": raw_tokens,
                "compressed_tokens": raw_tokens,
                "budget_tokens": token_budget,
            }

        recent_count = min(self.compression_keep_recent, len(current_history))
        
        # Adjust recent_count to avoid slicing between assistant(tool_calls) and tool messages
        while recent_count < len(current_history):
            # Check the first message in our potential 'recent' slice
            first_in_slice = current_history[-recent_count]
            if first_in_slice.get("role") == "tool":
                # If it's a tool message, we must include the preceding messages 
                # until we find the assistant message that triggered it.
                recent_count += 1
            else:
                break

        recent_messages = current_history[-recent_count:] if recent_count else []
        older_messages = current_history[:-recent_count] if recent_count else current_history

        # Preserve the most recent raw turns whenever possible; only if those
        # alone exceed the budget do we peel off the oldest kept messages.
        while recent_messages and self.estimate_messages_tokens(recent_messages) > max(
            token_budget, 0
        ):
            older_messages.append(recent_messages.pop(0))

        summary_budget = max(token_budget - self.estimate_messages_tokens(recent_messages), 0)
        summary_message = self._build_compression_summary(older_messages, summary_budget)

        compressed_messages: list[dict[str, Any]] = []
        if summary_message:
            compressed_messages.append(summary_message)
        compressed_messages.extend(recent_messages)

        compressed_tokens = self.estimate_messages_tokens(compressed_messages)
        return {
            "messages": compressed_messages,
            "applied": compressed_messages != current_history,
            "summary": summary_message["content"] if summary_message else "",
            "raw_count": len(current_history),
            "compressed_count": len(compressed_messages),
            "kept_recent_count": len(recent_messages),
            "dropped_count": max(len(current_history) - len(compressed_messages), 0),
            "raw_tokens": raw_tokens,
            "compressed_tokens": compressed_tokens,
            "budget_tokens": token_budget,
        }

    def estimate_messages_tokens(self, messages: list[dict[str, Any]]) -> int:
        """Estimate token usage using message text length as a cheap proxy."""
        return sum(self.estimate_text_tokens(self._message_text(message)) for message in messages)

    def estimate_text_tokens(self, text: str) -> int:
        """Approximate token count from characters without extra dependencies."""
        normalized = " ".join(str(text).split())
        if not normalized:
            return 0
        return max(1, (len(normalized) + 3) // 4)

    def _build_compression_summary(
        self,
        messages: list[dict[str, Any]],
        token_budget: int,
    ) -> dict[str, str] | None:
        """Fold older messages into one system summary within the budget."""
        if not messages or token_budget <= 0:
            return None

        prefix = "Runtime compression summary:"
        collected: list[str] = []
        for message in reversed(messages):
            line = self._summary_line(message)
            if not line:
                continue

            candidate = [line] + collected
            content = prefix + "\n- " + "\n- ".join(candidate)
            if self.estimate_text_tokens(content) > token_budget:
                continue
            collected = candidate

        if not collected:
            content = prefix + "\n- Earlier messages were omitted to fit the context window."
            if self.estimate_text_tokens(content) > token_budget:
                return None
            return {"role": "system", "content": content}

        return {"role": "system", "content": prefix + "\n- " + "\n- ".join(collected)}

    def _summary_line(self, message: dict[str, Any]) -> str:
        """Render one message as a short bullet inside the runtime summary."""
        role = str(message.get("role", "message")).strip().lower() or "message"
        content = self._excerpt_text(message.get("content", ""))

        if role == "assistant" and message.get("tool_calls"):
            tool_names = [
                tool_call.get("function", {}).get("name", "tool")
                for tool_call in message.get("tool_calls", [])
                if isinstance(tool_call, dict)
            ]
            requested = ", ".join(name for name in tool_names if name)
            if requested and content:
                return f"Assistant: {content} Requested tools: {requested}."
            if requested:
                return f"Assistant requested tools: {requested}."

        if role == "tool":
            if content:
                return f"Tool result: {content}"
            return "Tool result recorded."

        if content:
            return f"{role.capitalize()}: {content}"
        return ""

    def _message_text(self, message: dict[str, Any]) -> str:
        """Flatten one message for token estimation and debug reporting."""
        parts = [str(message.get("role", ""))]

        content = message.get("content")
        if content:
            parts.append(str(content))

        tool_call_id = message.get("tool_call_id")
        if tool_call_id:
            parts.append(str(tool_call_id))

        tool_calls = message.get("tool_calls")
        if tool_calls:
            parts.append(json.dumps(tool_calls, ensure_ascii=False, sort_keys=True))

        return "\n".join(part for part in parts if part)

    def _excerpt_text(self, value: object, limit: int = 160) -> str:
        """Collapse whitespace and trim long text for runtime summaries."""
        text = " ".join(str(value).split())
        if len(text) <= limit:
            return text
        return text[: limit - 3] + "..."
