from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class ModelStreamEventKind(str, Enum):
    """User-visible model stream event categories."""

    STATUS = "status"
    REASONING_SUMMARY = "reasoning_summary"
    CONTENT_DELTA = "content_delta"
    TOOL_CALL_DELTA = "tool_call_delta"
    TOOL_RESULT = "tool_result"
    USAGE = "usage"
    FINAL_MESSAGE = "final_message"
    ERROR = "error"


@dataclass(frozen=True)
class ModelStreamEvent:
    """A normalized stream event emitted above provider-specific SDK chunks."""

    kind: ModelStreamEventKind
    text: str = ""
    tool_call_id: str | None = None
    tool_name: str | None = None
    arguments_delta: str = ""
    usage: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def status(cls, text: str, **metadata: Any) -> "ModelStreamEvent":
        return cls(ModelStreamEventKind.STATUS, text=text, metadata=metadata)

    @classmethod
    def reasoning_summary(cls, text: str, **metadata: Any) -> "ModelStreamEvent":
        return cls(ModelStreamEventKind.REASONING_SUMMARY, text=text, metadata=metadata)

    @classmethod
    def content_delta(cls, text: str, **metadata: Any) -> "ModelStreamEvent":
        return cls(ModelStreamEventKind.CONTENT_DELTA, text=text, metadata=metadata)

    @classmethod
    def tool_call_delta(
        cls,
        *,
        tool_call_id: str | None = None,
        tool_name: str | None = None,
        arguments_delta: str = "",
        **metadata: Any,
    ) -> "ModelStreamEvent":
        return cls(
            ModelStreamEventKind.TOOL_CALL_DELTA,
            tool_call_id=tool_call_id,
            tool_name=tool_name,
            arguments_delta=arguments_delta,
            metadata=metadata,
        )

    @classmethod
    def tool_result(
        cls,
        text: str,
        *,
        tool_call_id: str | None = None,
        tool_name: str | None = None,
        **metadata: Any,
    ) -> "ModelStreamEvent":
        return cls(
            ModelStreamEventKind.TOOL_RESULT,
            text=text,
            tool_call_id=tool_call_id,
            tool_name=tool_name,
            metadata=metadata,
        )

    @classmethod
    def usage_delta(
        cls,
        usage: dict[str, Any],
        **metadata: Any,
    ) -> "ModelStreamEvent":
        return cls(ModelStreamEventKind.USAGE, usage=dict(usage), metadata=metadata)

    @classmethod
    def final_message(cls, text: str, **metadata: Any) -> "ModelStreamEvent":
        return cls(ModelStreamEventKind.FINAL_MESSAGE, text=text, metadata=metadata)

    @classmethod
    def error(cls, text: str, **metadata: Any) -> "ModelStreamEvent":
        return cls(ModelStreamEventKind.ERROR, text=text, metadata=metadata)

    def to_record(self) -> dict[str, Any]:
        return {
            "kind": self.kind.value,
            "text": self.text,
            "tool_call_id": self.tool_call_id,
            "tool_name": self.tool_name,
            "arguments_delta": self.arguments_delta,
            "usage": dict(self.usage),
            "metadata": dict(self.metadata),
        }
