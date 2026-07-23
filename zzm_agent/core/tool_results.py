from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Protocol

from zzm_agent.core.runtime_records import EventBus
from zzm_agent.security.content import ContentTrust, redact_secrets, trust_metadata


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class DisplayMode(str, Enum):
    """How a tool result should be presented by user interfaces."""

    INLINE = "inline"
    COLLAPSED = "collapsed"
    STREAMING = "streaming"
    SUMMARY_ONLY = "summary_only"
    HIDDEN = "hidden"


@dataclass
class DisplayPolicy:
    """Preview and folding policy for one rendered tool output."""

    default_mode: DisplayMode | str = DisplayMode.INLINE
    max_preview_chars: int = 4000
    max_preview_lines: int = 80
    preserve_realtime: bool = False
    create_artifact: bool = False
    user_expandable: bool = True

    def normalized_mode(self) -> DisplayMode:
        if isinstance(self.default_mode, DisplayMode):
            return self.default_mode
        return DisplayMode(str(self.default_mode))


def _preview_text(text: str, policy: DisplayPolicy) -> tuple[str, bool, int, int]:
    max_chars = max(0, int(policy.max_preview_chars))
    max_lines = max(0, int(policy.max_preview_lines))
    lines = text.splitlines()
    preview_lines = lines[:max_lines] if max_lines else []
    preview = "\n".join(preview_lines)
    hidden_lines = max(0, len(lines) - len(preview_lines))

    hidden_chars = 0
    if len(preview) > max_chars:
        hidden_chars += len(preview) - max_chars
        preview = preview[:max_chars]
    hidden_chars += max(0, len(text) - len(preview))
    truncated = hidden_lines > 0 or hidden_chars > 0
    return preview, truncated, hidden_lines, hidden_chars


@dataclass
class ToolResult:
    """Structured result for one tool call.

    model_content is the content sent back to the model. display_content is the
    UI-facing preview and folding metadata. Artifacts point to full results.
    """

    tool_call_id: str
    tool_name: str
    status: str
    model_content: str
    display_content: dict[str, Any] = field(default_factory=dict)
    artifacts: list[dict[str, Any]] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)
    display_mode: DisplayMode | str = DisplayMode.INLINE
    content_trust: ContentTrust | str = ContentTrust.UNTRUSTED
    content_source: str = "tool"

    @classmethod
    def from_text(
        cls,
        *,
        tool_call_id: str,
        tool_name: str,
        status: str,
        content: str,
        policy: DisplayPolicy | None = None,
        artifacts: list[dict[str, Any]] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> "ToolResult":
        display_policy = policy or DisplayPolicy()
        preview, truncated, hidden_lines, hidden_chars = _preview_text(content, display_policy)
        mode = display_policy.normalized_mode()
        if truncated and mode is DisplayMode.INLINE:
            mode = DisplayMode.COLLAPSED
        return cls(
            tool_call_id=tool_call_id,
            tool_name=tool_name,
            status=status,
            model_content=content,
            display_content={
                "text": preview,
                "truncated": truncated,
                "hidden_lines": hidden_lines,
                "hidden_chars": hidden_chars,
                "user_expandable": display_policy.user_expandable,
            },
            artifacts=list(artifacts or []),
            metadata={
                **dict(metadata or {}),
                **trust_metadata(source=tool_name, trust=ContentTrust.UNTRUSTED),
            },
            display_mode=mode,
            content_trust=ContentTrust.UNTRUSTED,
            content_source=tool_name,
        )

    def to_model_message(self) -> dict[str, str]:
        return {
            "role": "tool",
            "tool_call_id": self.tool_call_id,
            "content": str(redact_secrets(self.model_content)),
        }

    def to_record(self) -> dict[str, Any]:
        return redact_secrets({
            "tool_call_id": self.tool_call_id,
            "tool_name": self.tool_name,
            "status": self.status,
            "model_content": self.model_content,
            "display_content": dict(self.display_content),
            "artifacts": list(self.artifacts),
            "metadata": dict(self.metadata),
            "display_mode": (
                self.display_mode.value
                if isinstance(self.display_mode, DisplayMode)
                else str(self.display_mode)
            ),
            "content_trust": (
                self.content_trust.value
                if isinstance(self.content_trust, ContentTrust)
                else str(self.content_trust)
            ),
            "content_source": self.content_source,
        })

    @classmethod
    def from_record(cls, record: dict[str, Any]) -> "ToolResult":
        return cls(
            tool_call_id=str(record["tool_call_id"]),
            tool_name=str(record.get("tool_name") or "<unknown>"),
            status=str(record.get("status") or "unknown"),
            model_content=str(record.get("model_content") or ""),
            display_content=dict(record.get("display_content") or {}),
            artifacts=list(record.get("artifacts") or []),
            metadata=dict(record.get("metadata") or {}),
            display_mode=DisplayMode(record.get("display_mode", DisplayMode.INLINE.value)),
            content_trust=ContentTrust(
                record.get("content_trust", ContentTrust.UNTRUSTED.value)
            ),
            content_source=str(record.get("content_source") or record.get("tool_name") or "tool"),
        )


@dataclass
class ToolProgressEvent:
    """Progress update emitted between tool start and final result events."""

    tool_call_id: str
    sequence: int
    message: str = ""
    percent: float | None = None
    stdout_chunk: str | None = None
    stderr_chunk: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    timestamp: str = field(default_factory=_utc_now_iso)

    def __post_init__(self) -> None:
        if self.sequence < 1:
            raise ValueError("ToolProgressEvent sequence must be >= 1.")
        if self.percent is not None and not 0 <= float(self.percent) <= 100:
            raise ValueError("ToolProgressEvent percent must be between 0 and 100.")

    def to_record(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_record(cls, record: dict[str, Any]) -> "ToolProgressEvent":
        return cls(
            tool_call_id=str(record["tool_call_id"]),
            sequence=int(record["sequence"]),
            message=str(record.get("message") or ""),
            percent=record.get("percent"),
            stdout_chunk=record.get("stdout_chunk"),
            stderr_chunk=record.get("stderr_chunk"),
            metadata=dict(record.get("metadata") or {}),
            timestamp=str(record.get("timestamp") or _utc_now_iso()),
        )


class ToolProgressEmitter:
    """Emit ordered tool progress events and keep a bounded recent buffer."""

    def __init__(
        self,
        event_bus: EventBus | None = None,
        *,
        max_buffered_events: int = 100,
    ) -> None:
        self.event_bus = event_bus
        self.max_buffered_events = max(1, int(max_buffered_events))
        self._sequences: dict[str, int] = {}
        self._buffers: dict[str, list[ToolProgressEvent]] = {}

    def emit(
        self,
        *,
        tool_call_id: str,
        message: str = "",
        percent: float | None = None,
        stdout_chunk: str | None = None,
        stderr_chunk: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> ToolProgressEvent:
        sequence = self._sequences.get(tool_call_id, 0) + 1
        self._sequences[tool_call_id] = sequence
        event = ToolProgressEvent(
            tool_call_id=tool_call_id,
            sequence=sequence,
            message=message,
            percent=percent,
            stdout_chunk=stdout_chunk,
            stderr_chunk=stderr_chunk,
            metadata=dict(metadata or {}),
        )
        buffer = self._buffers.setdefault(tool_call_id, [])
        buffer.append(event)
        if len(buffer) > self.max_buffered_events:
            del buffer[: len(buffer) - self.max_buffered_events]
        if self.event_bus is not None:
            self.event_bus.publish("tool.progress", event.to_record())
        return event

    def events_for(self, tool_call_id: str) -> list[ToolProgressEvent]:
        return list(self._buffers.get(tool_call_id, []))


@dataclass
class ToolRenderContext:
    """Context provided to a renderer without granting execution authority."""

    tool_name: str
    tool_call_id: str
    arguments_summary: dict[str, Any] = field(default_factory=dict)
    risk_level: str = "unknown"
    source: str = "local"
    category: str = "default"


@dataclass
class RenderedToolView:
    """Plain render output shared by Rich, terminal, and future UI adapters."""

    text: str
    display_mode: DisplayMode | str = DisplayMode.INLINE
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_record(self) -> dict[str, Any]:
        return {
            "text": self.text,
            "display_mode": (
                self.display_mode.value
                if isinstance(self.display_mode, DisplayMode)
                else str(self.display_mode)
            ),
            "metadata": dict(self.metadata),
        }


class ToolRenderer(Protocol):
    """Renderer protocol. Renderers consume facts and never execute tools."""

    def render_use(self, context: ToolRenderContext) -> RenderedToolView:
        ...

    def render_progress(
        self,
        context: ToolRenderContext,
        event: ToolProgressEvent,
    ) -> RenderedToolView:
        ...

    def render_result(
        self,
        context: ToolRenderContext,
        result: ToolResult,
    ) -> RenderedToolView:
        ...

    def render_error(
        self,
        context: ToolRenderContext,
        result: ToolResult,
    ) -> RenderedToolView:
        ...


class PlainTextToolRenderer:
    """Default renderer used when no tool-specific renderer is registered."""

    def render_use(self, context: ToolRenderContext) -> RenderedToolView:
        return RenderedToolView(
            text=f"Using {context.tool_name} ({context.risk_level})",
            display_mode=DisplayMode.INLINE,
        )

    def render_progress(
        self,
        context: ToolRenderContext,
        event: ToolProgressEvent,
    ) -> RenderedToolView:
        prefix = f"{context.tool_name} progress #{event.sequence}"
        if event.percent is not None:
            prefix = f"{prefix} {event.percent:g}%"
        chunks = [value for value in (event.message, event.stdout_chunk, event.stderr_chunk) if value]
        return RenderedToolView(text=f"{prefix}: {' '.join(chunks)}".rstrip(": "))

    def render_result(
        self,
        context: ToolRenderContext,
        result: ToolResult,
    ) -> RenderedToolView:
        text = str(result.display_content.get("text") or "")
        if result.display_content.get("truncated"):
            text = (
                f"{text}\n"
                f"[truncated: {result.display_content.get('hidden_lines', 0)} lines, "
                f"{result.display_content.get('hidden_chars', 0)} chars hidden]"
            ).strip()
        if result.artifacts:
            artifact_ids = [
                str(record.get("artifact_id") or record.get("path") or "<artifact>")
                for record in result.artifacts
            ]
            text = f"{text}\nArtifacts: {', '.join(artifact_ids)}".strip()
        return RenderedToolView(
            text=text,
            display_mode=result.display_mode,
            metadata={"status": result.status},
        )

    def render_error(
        self,
        context: ToolRenderContext,
        result: ToolResult,
    ) -> RenderedToolView:
        view = self.render_result(context, result)
        return RenderedToolView(
            text=f"Error in {context.tool_name}: {view.text}",
            display_mode=view.display_mode,
            metadata=view.metadata,
        )


class RendererRegistry:
    """Select tool renderers by exact tool name, category, source, or fallback."""

    def __init__(self, default_renderer: ToolRenderer | None = None) -> None:
        self.default_renderer = default_renderer or PlainTextToolRenderer()
        self._by_tool_name: dict[str, ToolRenderer] = {}
        self._by_category: dict[str, ToolRenderer] = {}
        self._by_source: dict[str, ToolRenderer] = {}

    def register_tool(self, tool_name: str, renderer: ToolRenderer) -> None:
        self._by_tool_name[tool_name] = renderer

    def register_category(self, category: str, renderer: ToolRenderer) -> None:
        self._by_category[category] = renderer

    def register_source(self, source: str, renderer: ToolRenderer) -> None:
        self._by_source[source] = renderer

    def select(self, context: ToolRenderContext) -> ToolRenderer:
        if context.tool_name in self._by_tool_name:
            return self._by_tool_name[context.tool_name]
        if context.category in self._by_category:
            return self._by_category[context.category]
        if context.source in self._by_source:
            return self._by_source[context.source]
        return self.default_renderer
