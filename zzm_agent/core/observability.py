from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from zzm_agent.constants import EVENT_TOOL_END, EVENT_TOOL_ERROR, EVENT_TOOL_START


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
    return {
        str(key): _summarize_value(value, max_length=max_length)
        for key, value in sorted(arguments.items())
    }


def preview_text(value: Any, max_length: int = 240) -> str:
    """Return a single-line preview for tool results and errors."""
    text = str(value).replace("\r\n", "\n").replace("\n", "\\n")
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
    source: str = "unavailable"

    def add(self, other: "TokenUsage") -> None:
        """Accumulate another usage sample into this one."""
        self.prompt_tokens += other.prompt_tokens
        self.completion_tokens += other.completion_tokens
        self.total_tokens += other.total_tokens
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
            source=self.source,
        )

    def has_tokens(self) -> bool:
        """Return whether any usage value is available."""
        return self.total_tokens > 0 or self.prompt_tokens > 0 or self.completion_tokens > 0

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
