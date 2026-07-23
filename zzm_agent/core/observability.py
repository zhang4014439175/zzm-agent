from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from enum import Enum
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from zzm_agent.constants import EVENT_TOOL_END, EVENT_TOOL_ERROR, EVENT_TOOL_START
from zzm_agent.security.content import redact_secrets


ToolEventCallback = Callable[["ToolEvent"], None]


def utc_now_iso() -> str:
    """Return a stable UTC timestamp for logs and event records."""
    return datetime.now(timezone.utc).isoformat()


def _summarize_value(value: Any, max_length: int = 160) -> Any:
    """Return a compact, JSON-friendly preview of one tool argument."""
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    if isinstance(value, (list, tuple)):
        return [_summarize_value(item, max_length=max_length) for item in value[:5]]
    if isinstance(value, dict):
        return summarize_arguments(value, max_length=max_length)

    text = str(value).replace("\r\n", "\n")
    if len(text) <= max_length:
        return text
    return f"{text[:max_length]}... ({len(text)} chars)"


def summarize_arguments(
    arguments: dict[str, Any],
    max_length: int = 160,
) -> dict[str, Any]:
    """Summarize tool arguments without logging very large payloads verbatim."""
    return redact_secrets({
        str(key): _summarize_value(value, max_length=max_length)
        for key, value in sorted(arguments.items())
    })


def preview_text(value: Any, max_length: int = 240) -> str:
    """Return a single-line preview for tool results and errors."""
    text = str(redact_secrets(value)).replace("\r\n", "\n").replace("\n", "\\n")
    if len(text) <= max_length:
        return text
    return f"{text[:max_length]}... ({len(text)} chars)"


@dataclass
class ToolEvent:
    """Structured event emitted around one tool execution."""

    event_name: str
    tool_name: str
    tool_call_id: str
    arguments_summary: dict[str, Any]
    timestamp: str
    risk_level: str = "unknown"
    status: str = "running"
    duration_ms: float | None = None
    attempts: int | None = None
    result_preview: str | None = None
    error_type: str | None = None
    error_message: str | None = None

    def to_record(self) -> dict[str, Any]:
        """Serialize the event for JSONL logs."""
        return asdict(self)


@dataclass
class TokenUsage:
    """Token usage for one or more model calls."""

    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    cache_creation_tokens: int = 0
    cache_read_tokens: int = 0
    reasoning_tokens: int = 0
    tool_schema_tokens: int = 0
    model_calls: int = 0
    tool_calls: int = 0
    source: str = "unavailable"

    def add(self, other: "TokenUsage") -> None:
        """Accumulate another usage sample into this one."""
        self.prompt_tokens += other.prompt_tokens
        self.completion_tokens += other.completion_tokens
        self.total_tokens += other.total_tokens
        self.cache_creation_tokens += other.cache_creation_tokens
        self.cache_read_tokens += other.cache_read_tokens
        self.reasoning_tokens += other.reasoning_tokens
        self.tool_schema_tokens += other.tool_schema_tokens
        self.model_calls += other.model_calls
        self.tool_calls += other.tool_calls
        if other.source == "unavailable":
            return
        if self.source in {"unavailable", other.source}:
            self.source = other.source
        else:
            self.source = "mixed"

    def copy(self) -> "TokenUsage":
        """Return a detached copy."""
        return TokenUsage(
            prompt_tokens=self.prompt_tokens,
            completion_tokens=self.completion_tokens,
            total_tokens=self.total_tokens,
            cache_creation_tokens=self.cache_creation_tokens,
            cache_read_tokens=self.cache_read_tokens,
            reasoning_tokens=self.reasoning_tokens,
            tool_schema_tokens=self.tool_schema_tokens,
            model_calls=self.model_calls,
            tool_calls=self.tool_calls,
            source=self.source,
        )

    def has_tokens(self) -> bool:
        """Return whether any usage value is available."""
        return self.total_tokens > 0 or self.prompt_tokens > 0 or self.completion_tokens > 0

    def to_record(self) -> dict[str, Any]:
        """Serialize usage for persisted state and debug records."""
        return redact_secrets(asdict(self))

    @classmethod
    def from_record(cls, record: dict[str, Any] | None) -> "TokenUsage":
        """Restore usage while tolerating records created before 6.5."""
        if not record:
            return cls()
        allowed = cls.__dataclass_fields__.keys()
        values = {key: record[key] for key in allowed if key in record}
        return cls(**values)

    def estimated_cost_usd(
        self,
        input_price_per_1m: float = 0.0,
        output_price_per_1m: float = 0.0,
    ) -> float:
        """Estimate cost from per-million-token pricing."""
        return (
            (self.prompt_tokens / 1_000_000) * input_price_per_1m
            + (self.completion_tokens / 1_000_000) * output_price_per_1m
        )


class UsageScope(str, Enum):
    """Aggregation scope for model and tool usage."""

    MODEL = "model"
    TURN = "turn"
    CONVERSATION = "conversation"
    TASK = "task"
    APPLICATION = "application"


@dataclass
class UsageState:
    """Multi-scope usage accounting for one agent runtime.

    The state keeps old turn/session counters possible through TokenUsage, while
    also exposing explicit model, turn, conversation, task, and application totals.
    """

    turn: TokenUsage = field(default_factory=TokenUsage)
    conversation: TokenUsage = field(default_factory=TokenUsage)
    task: TokenUsage = field(default_factory=TokenUsage)
    application: TokenUsage = field(default_factory=TokenUsage)
    by_model: dict[str, TokenUsage] = field(default_factory=dict)
    current_turn_id: str | None = None
    conversation_id: str | None = None
    task_id: str | None = None

    def start_turn(self, turn_id: str | None = None) -> None:
        """Reset turn-scoped usage for a new user turn."""
        self.current_turn_id = turn_id
        self.turn = TokenUsage()

    def set_conversation(self, conversation_id: str | None) -> None:
        """Attach usage to the active conversation/session."""
        self.conversation_id = conversation_id

    def set_task(self, task_id: str | None) -> None:
        """Attach usage to the active task when a planner/task layer exists."""
        self.task_id = task_id

    def record_model_call(
        self,
        usage: TokenUsage,
        *,
        model: str,
        tool_schema_tokens: int = 0,
    ) -> TokenUsage:
        """Record one model call across all active usage scopes."""
        sample = usage.copy()
        sample.model_calls += 1
        sample.tool_schema_tokens += max(0, int(tool_schema_tokens or 0))
        self._add_to_scopes(sample, model=model)
        return sample

    def record_tool_calls(self, count: int) -> TokenUsage:
        """Record tool call count across non-model aggregate scopes."""
        sample = TokenUsage(tool_calls=max(0, int(count or 0)))
        self.turn.add(sample)
        self.conversation.add(sample)
        self.task.add(sample)
        self.application.add(sample)
        return sample

    def _add_to_scopes(self, usage: TokenUsage, *, model: str) -> None:
        self.turn.add(usage)
        self.conversation.add(usage)
        self.task.add(usage)
        self.application.add(usage)
        self.by_model.setdefault(model, TokenUsage()).add(usage)

    def snapshot(self, scope: UsageScope | str) -> TokenUsage:
        """Return a detached usage total for one aggregate scope."""
        scope_value = UsageScope(scope)
        if scope_value is UsageScope.TURN:
            return self.turn.copy()
        if scope_value is UsageScope.CONVERSATION:
            return self.conversation.copy()
        if scope_value is UsageScope.TASK:
            return self.task.copy()
        if scope_value is UsageScope.APPLICATION:
            return self.application.copy()
        raise ValueError("Model usage requires snapshot_for_model(model).")

    def snapshot_for_model(self, model: str) -> TokenUsage:
        """Return a detached usage total for one model name."""
        return self.by_model.get(model, TokenUsage()).copy()

    def to_record(self) -> dict[str, Any]:
        """Serialize the full usage state for session/task persistence."""
        return {
            "turn": self.turn.to_record(),
            "conversation": self.conversation.to_record(),
            "task": self.task.to_record(),
            "application": self.application.to_record(),
            "by_model": {
                model: usage.to_record()
                for model, usage in sorted(self.by_model.items())
            },
            "current_turn_id": self.current_turn_id,
            "conversation_id": self.conversation_id,
            "task_id": self.task_id,
        }

    @classmethod
    def from_record(cls, record: dict[str, Any] | None) -> "UsageState":
        """Restore UsageState while tolerating missing fields."""
        if not record:
            return cls()
        state = cls(
            turn=TokenUsage.from_record(record.get("turn")),
            conversation=TokenUsage.from_record(record.get("conversation")),
            task=TokenUsage.from_record(record.get("task")),
            application=TokenUsage.from_record(record.get("application")),
            current_turn_id=record.get("current_turn_id"),
            conversation_id=record.get("conversation_id"),
            task_id=record.get("task_id"),
        )
        for model, usage_record in dict(record.get("by_model") or {}).items():
            state.by_model[str(model)] = TokenUsage.from_record(usage_record)
        return state


class ToolEventLogger:
    """Append tool execution events to a JSONL file."""

    def __init__(self, path: str | Path):
        self.path = Path(path)

    def __call__(self, event: ToolEvent) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(event.to_record(), ensure_ascii=False, sort_keys=True))
            handle.write("\n")


def read_tool_event_log(path: str | Path) -> list[dict[str, Any]]:
    """Read a JSONL tool event log into dictionaries."""
    log_path = Path(path)
    if not log_path.exists():
        return []
    records: list[dict[str, Any]] = []
    with log_path.open(encoding="utf-8") as handle:
        for line in handle:
            stripped = line.strip()
            if not stripped:
                continue
            records.append(json.loads(stripped))
    return records


def tool_start_event(
    tool_name: str,
    tool_call_id: str,
    arguments: dict[str, Any],
    risk_level: str,
) -> ToolEvent:
    return ToolEvent(
        event_name=EVENT_TOOL_START,
        tool_name=tool_name,
        tool_call_id=tool_call_id,
        arguments_summary=summarize_arguments(arguments),
        timestamp=utc_now_iso(),
        risk_level=risk_level,
        status="running",
    )


def tool_end_event(
    tool_name: str,
    tool_call_id: str,
    arguments: dict[str, Any],
    risk_level: str,
    status: str,
    duration_ms: float,
    result: str,
    attempts: int,
) -> ToolEvent:
    return ToolEvent(
        event_name=EVENT_TOOL_END,
        tool_name=tool_name,
        tool_call_id=tool_call_id,
        arguments_summary=summarize_arguments(arguments),
        timestamp=utc_now_iso(),
        risk_level=risk_level,
        status=status,
        duration_ms=duration_ms,
        attempts=attempts,
        result_preview=preview_text(result),
    )


def tool_error_event(
    tool_name: str,
    tool_call_id: str,
    arguments: dict[str, Any],
    risk_level: str,
    duration_ms: float,
    error_type: str,
    error_message: str,
    attempts: int,
    result: str | None = None,
) -> ToolEvent:
    return ToolEvent(
        event_name=EVENT_TOOL_ERROR,
        tool_name=tool_name,
        tool_call_id=tool_call_id,
        arguments_summary=summarize_arguments(arguments),
        timestamp=utc_now_iso(),
        risk_level=risk_level,
        status="error",
        duration_ms=duration_ms,
        attempts=attempts,
        result_preview=preview_text(result) if result is not None else None,
        error_type=error_type,
        error_message=preview_text(error_message),
    )
