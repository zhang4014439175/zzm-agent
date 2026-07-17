from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from zzm_agent.core.context_budget import ContextBudget, ContextBudgetEntry
from zzm_agent.core.observability import UsageState
from zzm_agent.core.state.support import MemoryLoadState
from zzm_agent.memory.episodic_store import EpisodicStore
from zzm_agent.memory.history_store import HistoryStore
from zzm_agent.memory.instructions import InstructionManager
from zzm_agent.memory.io import StorageIO
from zzm_agent.memory.pinned_context import PinnedContext
from zzm_agent.memory.retriever import KeywordMemoryRetriever, MemoryRetriever
from zzm_agent.memory.semantic_store import SemanticStore
from zzm_agent.memory.session_store import SessionStore
from zzm_agent.memory.token_counter import TokenCounter


class MemoryStore:
    """Compose session, history, episodic, and semantic memory storage."""

    def __init__(
        self,
        path: str | Path,
        max_history: int = 50,
        session_id: str | None = None,
        retrieval_top_k: int = 3,
        max_context_tokens: int = 32000,
        compression_keep_recent: int = 10,
        model_name: str | None = None,
        token_counter: TokenCounter | None = None,
        workspace_root: str | Path | None = None,
        instruction_filenames: tuple[str, ...] = ("AGENTS.md", "ZZM.md"),
        instruction_max_chars: int = 8000,
        auto_memory_enabled: bool = True,
    ):
        self.max_history = max_history
        self.retrieval_top_k = retrieval_top_k
        self.max_context_tokens = max_context_tokens
        self.compression_keep_recent = compression_keep_recent
        self.token_counter = token_counter or TokenCounter(model=model_name)
        self.workspace_root = Path(workspace_root).resolve() if workspace_root else None
        self.instruction_filenames = tuple(instruction_filenames)
        self.instruction_max_chars = instruction_max_chars
        self.auto_memory_enabled = auto_memory_enabled

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
        self.memory_load_state = MemoryLoadState()
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

    @property
    def latest_context_path(self) -> Path:
        """Return the path of the active session's latest context snapshot."""
        return self.sessions.latest_context_path()

    def load_history(self) -> list[dict]:
        """Load the recent transcript for the active session."""
        return self._drop_orphan_tool_results(self.history_store.load_history())

    def append(self, messages: list[dict]) -> None:
        """Append messages to the active session and refresh episodic memory."""
        history = self.history_store.append(messages)
        self.episodic_store.update(self.session_id, history=history)

    def save_latest_context(self, snapshot: dict[str, Any]) -> None:
        """Persist the latest model prompt snapshot inside the active session."""
        payload = {
            "created_at": self.sessions.utc_now(),
            "session_id": self.session_id,
            **snapshot,
        }
        self.io.write_json(self.latest_context_path, payload)

    def save_usage_state(self, usage_state: UsageState) -> None:
        """Persist usage accounting in the active session metadata."""
        meta = self.sessions.ensure_session(self.session_id)
        meta["usage_state"] = usage_state.to_record()
        meta["updated_at"] = self.sessions.utc_now()
        self.io.write_json(self.meta_path, meta)
        self.sessions.upsert_index(meta)

    def load_usage_state(self) -> UsageState:
        """Load usage accounting from the active session metadata."""
        meta = self.sessions.load_meta(self.session_id) or {}
        usage_state = UsageState.from_record(meta.get("usage_state"))
        usage_state.set_conversation(self.session_id)
        return usage_state

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

    def list_semantic_memory(self, *, include_disabled: bool = True) -> list[dict]:
        """Return semantic memory entries including source and enabled metadata."""
        return self.semantic_store.load(include_disabled=include_disabled)

    def list_semantic_facts(self) -> list[str]:
        """Return every long-term semantic memory fact ordered by recency."""
        return self.semantic_store.list_facts()

    def remember_fact(self, fact: str, *, source: str = "manual") -> dict:
        """Insert or refresh one semantic memory fact."""
        return self.semantic_store.remember(fact, now=self.sessions.utc_now(), source=source)

    def forget_fact(self, keyword: str) -> int:
        """Remove semantic memory entries whose text matches the keyword."""
        return self.semantic_store.forget(keyword)

    def set_memory_enabled(self, keyword: str, enabled: bool) -> int:
        """Enable or disable semantic memory entries matching a keyword."""
        return self.semantic_store.set_enabled(
            keyword,
            enabled=enabled,
            now=self.sessions.utc_now(),
        )

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
        memory_load_state = MemoryLoadState()
        max_items = limit if limit is not None else self.retrieval_top_k
        if max_items <= 0 or not self.auto_memory_enabled:
            self.memory_load_state = memory_load_state
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

        self._record_memory_file_version(
            memory_load_state,
            path=self.semantic_path,
            source_type="project_memory",
        )
        semantic_lines = []
        for entry in semantic_entries:
            if entry.get("fact") and memory_load_state.record_semantic_memory(entry):
                semantic_lines.append(entry["fact"])

        episodic_lines = []
        for entry in episodic_entries:
            session_id = entry.get("session_id", "unknown-session")
            self._record_memory_file_version(
                memory_load_state,
                path=self.sessions.episodic_path(session_id),
                source_type="nested_memory",
            )
            summary = entry.get("summary", "").strip()
            if summary and memory_load_state.record_episodic_memory(entry):
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
        self.memory_load_state = memory_load_state
        return messages

    def build_instruction_messages(
        self,
        *,
        cwd: str | Path | None = None,
        memory_load_state: MemoryLoadState | None = None,
    ) -> list[dict[str, str]]:
        """Build system messages from AGENTS.md / ZZM.md style instruction files."""
        if self.workspace_root is None:
            return []

        state = memory_load_state or MemoryLoadState()
        manager = InstructionManager(
            workspace_root=self.workspace_root,
            cwd=cwd or self.workspace_root,
            filenames=self.instruction_filenames,
            max_chars=self.instruction_max_chars,
        )
        files = manager.load()
        if not files:
            if memory_load_state is None:
                self.memory_load_state = state
            return []

        blocks = []
        for item in files:
            path = str(item.path.resolve(strict=False))
            notice = ""
            if item.truncated:
                notice = (
                    f"\n[truncated: loaded {item.loaded_chars}/{item.original_chars} chars]"
                )
            state.record_file_source(
                path=path,
                source_type="project_instruction",
                version=item.version,
                content=item.content,
            )
            blocks.append(
                f"[priority {item.priority}] {item.name} ({path})"
                f"{notice}\n{item.content.strip()}"
            )

        if memory_load_state is None:
            self.memory_load_state = state
        return [
            {
                "role": "system",
                "content": (
                    "Project instructions loaded from repository files. "
                    "Nearest files have higher priority and may override broader guidance.\n\n"
                    + "\n\n---\n\n".join(blocks)
                ),
            }
        ]

    def list_instruction_files(self, *, cwd: str | Path | None = None) -> list[Any]:
        """Return loaded project instruction files for diagnostics."""
        if self.workspace_root is None:
            return []
        manager = InstructionManager(
            workspace_root=self.workspace_root,
            cwd=cwd or self.workspace_root,
            filenames=self.instruction_filenames,
            max_chars=self.instruction_max_chars,
        )
        return manager.load()

    def build_turn_messages(
        self,
        system_prompt: str,
        user_input: str,
        memory_limit: int | None = None,
        *,
        tool_schema_tokens: int = 0,
        output_reserve_tokens: int = 0,
        runtime_instruction_tokens: int = 0,
        prompt_cache_strategy: str = "stable_prefix",
    ) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        """组装一次模型请求，并返回消息列表与可解释的上下文预算。

        组装顺序为系统提示词、项目指令、检索记忆、PinnedContext、压缩后的历史和
        当前用户输入。计算历史可用空间时会预先扣除工具 Schema、运行时指令与
        模型输出预留，避免“消息看似未超限，但发送时加上工具定义后超限”。

        返回的第二项包含压缩结果、各来源 Token 明细、Prompt Cache 策略以及
        指令文件、记忆、压缩历史和 Artifact 等来源记录。Token 数均为本地估算，
        最终账单仍以 Provider usage 为准；当历史预算为零时会尽量保留由
        PinnedContext 提取的关键事实。
        """
        # 1. 先装入不可随意删除的固定上下文和跨压缩关键事实。
        history = self.load_history()
        system_messages = [{"role": "system", "content": system_prompt}]
        messages: list[dict[str, Any]] = list(system_messages)
        memory_messages = self.build_memory_messages(query=user_input, limit=memory_limit)
        memory_load_state = self.memory_load_state
        instruction_messages = self.build_instruction_messages(
            memory_load_state=memory_load_state
        )
        messages.extend(instruction_messages)
        messages.extend(memory_messages)
        pinned = PinnedContext.from_turn(user_input=user_input, history=history)
        pinned_message = pinned.to_message()
        if pinned_message is not None:
            messages.append(pinned_message)

        # 2. 从总窗口扣除固定消息、工具定义、运行时控制和输出空间，余额留给历史。
        reserved_messages = messages + [{"role": "user", "content": user_input}]
        reserved_tokens = self.estimate_messages_tokens(reserved_messages)
        history_budget = max(
            self.max_context_tokens
            - reserved_tokens
            - max(0, int(tool_schema_tokens))
            - max(0, int(output_reserve_tokens))
            - max(0, int(runtime_instruction_tokens)),
            0,
        )
        compression = self.compress_history(history=history, budget_tokens=history_budget)
        messages.extend(compression["messages"])
        user_message = {"role": "user", "content": user_input}
        messages.append(user_message)

        # 3. 把压缩后的消息拆分类别，并建立使用者可查询的来源清单。
        compressed_history = list(compression["messages"])
        tool_result_messages = [
            message for message in compressed_history if message.get("role") == "tool"
        ]
        history_messages = [
            message for message in compressed_history if message.get("role") != "tool"
        ]
        sources = [
            {
                "source": source.source_type,
                "path": source.path,
                "version": source.version,
            }
            for source in memory_load_state.sources
            if source.path
        ]
        if memory_messages:
            sources.append(
                {
                    "source": "retrieved_memory",
                    "count": len(memory_messages),
                }
            )
        if pinned_message is not None:
            sources.append({"source": "pinned_context", "preserved": True})
        if compression.get("applied"):
            sources.append(
                {
                    "source": "compressed_history",
                    "raw_count": compression.get("raw_count", 0),
                    "kept_recent_count": compression.get("kept_recent_count", 0),
                    "strategy": compression.get("compression_strategy", "none"),
                }
            )
        artifact_ids = sorted(
            {
                artifact_id
                for message in compressed_history
                for artifact_id in re.findall(
                    r"\bArtifact\s+(artifact-[A-Za-z0-9]+)\b",
                    str(message.get("content") or ""),
                )
            }
        )
        sources.extend(
            {"source": "artifact", "artifact_id": artifact_id}
            for artifact_id in artifact_ids
        )
        # 4. 用统一数据模型生成预算事实，供 AgentLoop、快照和 CLI 共同消费。
        budget = ContextBudget(
            max_context_tokens=self.max_context_tokens,
            entries=(
                ContextBudgetEntry(
                    "system_prompt",
                    self.estimate_messages_tokens(system_messages),
                    "base system prompt",
                ),
                ContextBudgetEntry(
                    "instruction_files",
                    self.estimate_messages_tokens(instruction_messages),
                    f"{len(instruction_messages)} instruction message(s)",
                ),
                ContextBudgetEntry(
                    "memory",
                    self.estimate_messages_tokens(memory_messages),
                    f"{len(memory_messages)} retrieved memory message(s)",
                ),
                ContextBudgetEntry(
                    "pinned_context",
                    self.estimate_messages_tokens([pinned_message])
                    if pinned_message is not None
                    else 0,
                    "facts preserved across compression",
                ),
                ContextBudgetEntry(
                    "history_messages",
                    self.estimate_messages_tokens(history_messages),
                    f"{len(history_messages)} compressed/recent message(s)",
                ),
                ContextBudgetEntry(
                    "tool_result",
                    self.estimate_messages_tokens(tool_result_messages),
                    f"{len(tool_result_messages)} tool result message(s)",
                ),
                ContextBudgetEntry(
                    "user_input",
                    self.estimate_messages_tokens([user_message]),
                    "current request or internal continuation",
                ),
                ContextBudgetEntry(
                    "tool_schema",
                    max(0, int(tool_schema_tokens)),
                    "available tool definitions",
                ),
                ContextBudgetEntry(
                    "reflection_prompt",
                    max(0, int(runtime_instruction_tokens)),
                    "runtime-only language, reflection, and continuation controls",
                ),
                ContextBudgetEntry(
                    "output_reserve",
                    max(0, int(output_reserve_tokens)),
                    "reserved model output capacity",
                ),
            ),
            compression_applied=bool(compression.get("applied")),
            compression_strategy=str(
                compression.get("compression_strategy", "none")
            ),
            prompt_cache_strategy=prompt_cache_strategy,
            sources=tuple(sources),
        )

        return messages, {
            **compression,
            "reserved_tokens": reserved_tokens,
            "max_context_tokens": self.max_context_tokens,
            "total_tokens": budget.total_tokens,
            "pinned_context": pinned_message["content"] if pinned_message else "",
            "memory_load_state": memory_load_state.to_record(),
            "budget": budget.to_record(),
            "budget_breakdown": {
                entry.source: entry.tokens for entry in budget.entries
            },
            "context_sources": list(budget.sources),
            "prompt_cache_strategy": budget.prompt_cache_strategy,
            "output_reserve_tokens": max(0, int(output_reserve_tokens)),
            "runtime_instruction_tokens": max(
                0, int(runtime_instruction_tokens)
            ),
            "tool_schema_tokens": max(0, int(tool_schema_tokens)),
        }

    def _record_memory_file_version(
        self,
        state: MemoryLoadState,
        *,
        path: Path,
        source_type: str,
    ) -> None:
        """Record the current file version for a memory source when available."""
        normalized_path = str(path.resolve(strict=False))
        if path.exists():
            stat = path.stat()
            version = f"{stat.st_mtime_ns}:{stat.st_size}"
        else:
            version = "missing"
        state.record_file_source(
            path=normalized_path,
            source_type=source_type,
            version=version,
        )

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
        current_history = self._drop_orphan_tool_results(
            list(history if history is not None else self.load_history())
        )
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
                "compression_strategy": "none",
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
                "compression_strategy": "none",
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
            recent_messages = self._drop_orphan_tool_results(recent_messages)

        summary_budget = max(token_budget - self.estimate_messages_tokens(recent_messages), 0)
        strategy = self._select_compression_strategy(raw_tokens, max(token_budget, 1))
        summary_message = self._build_compression_summary(
            older_messages,
            summary_budget,
            strategy=strategy,
        )

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
            "compression_strategy": strategy,
        }

    def _drop_orphan_tool_results(self, messages: list[dict]) -> list[dict]:
        """Remove tool results whose assistant tool call is outside the context."""
        kept: list[dict] = []
        available_tool_call_ids: set[str] = set()

        for message in messages:
            role = message.get("role")
            if role == "tool":
                tool_call_id = message.get("tool_call_id")
                if tool_call_id in available_tool_call_ids:
                    kept.append(message)
                    available_tool_call_ids.discard(tool_call_id)
                continue

            kept.append(message)
            if role == "assistant":
                for tool_call in message.get("tool_calls") or []:
                    if not isinstance(tool_call, dict):
                        continue
                    tool_call_id = tool_call.get("id")
                    if tool_call_id:
                        available_tool_call_ids.add(tool_call_id)

        return kept

    def estimate_messages_tokens(self, messages: list[dict[str, Any]]) -> int:
        """Estimate token usage using the configured tokenizer fallback chain."""
        return sum(self.estimate_text_tokens(self._message_text(message)) for message in messages)

    def estimate_text_tokens(self, text: str) -> int:
        """Estimate token count using model-specific, tiktoken, then len/4 fallback."""
        return self.token_counter.count_text(text)

    def token_count_source(self, text: str = "probe") -> str:
        """Return the current token counting strategy for diagnostics."""
        return self.token_counter.count(text).source

    def _select_compression_strategy(self, raw_tokens: int, token_budget: int) -> str:
        """Choose light, medium, or heavy compression based on budget pressure."""
        if token_budget <= 0:
            return "heavy"
        pressure = raw_tokens / token_budget
        if pressure <= 1.25:
            return "light"
        if pressure <= 2.5:
            return "medium"
        return "heavy"

    def _build_compression_summary(
        self,
        messages: list[dict[str, Any]],
        token_budget: int,
        strategy: str = "medium",
    ) -> dict[str, str] | None:
        """Fold older messages into one system summary within the budget."""
        if not messages or token_budget <= 0:
            return None

        prefix = f"Runtime compression summary ({strategy}):"
        line_limit = {"light": 220, "medium": 140, "heavy": 90}.get(strategy, 140)
        collected: list[str] = []
        for message in reversed(messages):
            line = self._summary_line(message, limit=line_limit)
            if not line:
                continue

            candidate = [line] + collected
            content = prefix + "\n- " + "\n- ".join(candidate)
            if self.estimate_text_tokens(content) > token_budget:
                continue
            collected = candidate

        if not collected:
            content = prefix + "\n- Earlier messages were compressed to fit the context window."
            if self.estimate_text_tokens(content) > token_budget:
                return None
            return {"role": "system", "content": content}

        return {"role": "system", "content": prefix + "\n- " + "\n- ".join(collected)}

    def _summary_line(self, message: dict[str, Any], limit: int = 160) -> str:
        """Render one message as a short bullet inside the runtime summary."""
        role = str(message.get("role", "message")).strip().lower() or "message"
        content = self._excerpt_text(message.get("content", ""), limit=limit)

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
