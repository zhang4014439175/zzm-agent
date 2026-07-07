from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable


@dataclass(frozen=True)
class ModelCapabilities:
    """Provider/model feature flags used by higher-level orchestration."""

    supports_streaming: bool = True
    supports_tool_calls: bool = True
    supports_reasoning: bool = False
    supports_json_schema: bool = False
    supports_vision: bool = False
    supports_parallel_tool_calls: bool = False
    supports_prompt_cache: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ModelRequest:
    """Normalized request payload before it is sent to a provider adapter."""

    model: str
    messages: list[dict[str, Any]]
    tools: list[dict[str, Any]] = field(default_factory=list)
    stream: bool = False
    temperature: float | None = None
    max_tokens: int | None = None
    tool_choice: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)

    def to_kwargs(self) -> dict[str, Any]:
        kwargs: dict[str, Any] = {
            "model": self.model,
            "messages": self.messages,
            **dict(self.extra),
        }
        if self.stream:
            kwargs["stream"] = True
        if self.temperature is not None:
            kwargs["temperature"] = self.temperature
        if self.max_tokens is not None:
            kwargs["max_tokens"] = self.max_tokens
        if self.tools:
            kwargs["tools"] = self.tools
        if self.tool_choice is not None:
            kwargs["tool_choice"] = self.tool_choice
        return kwargs


@dataclass(frozen=True)
class ModelResponse:
    """Provider-independent non-stream model response."""

    content: str
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    raw_usage: Any = None
    raw_response: Any = None


@dataclass(frozen=True)
class ModelToolCallDelta:
    """Incremental streamed tool-call fragment."""

    index: int
    tool_call_id: str | None = None
    name_delta: str = ""
    arguments_delta: str = ""


@dataclass(frozen=True)
class ModelStreamChunk:
    """Provider-independent streamed model chunk."""

    content_delta: str = ""
    reasoning_summary: str = ""
    tool_call_deltas: list[ModelToolCallDelta] = field(default_factory=list)
    raw_usage: Any = None
    raw_chunk: Any = None


class OpenAIChatCompletionsAdapter:
    """Normalize OpenAI-compatible chat completion responses."""

    def __init__(
        self,
        client: Any,
        *,
        capabilities: ModelCapabilities | None = None,
    ) -> None:
        self.client = client
        self.capabilities = capabilities or ModelCapabilities()

    def create_completion(self, kwargs: dict[str, Any]) -> Any:
        return self.client.chat.completions.create(**kwargs)

    def normalize_response(self, response: Any) -> ModelResponse:
        self._raise_for_error_payload(response)
        choices = getattr(response, "choices", None)
        if not choices:
            raise RuntimeError("Chat completion failed: response did not include choices.")
        message = getattr(choices[0], "message", None)
        if message is None:
            raise RuntimeError("Chat completion failed: response choice did not include a message.")
        return ModelResponse(
            content=getattr(message, "content", None) or "",
            tool_calls=[
                self._normalize_tool_call(tool_call)
                for tool_call in (getattr(message, "tool_calls", None) or [])
            ],
            raw_usage=getattr(response, "usage", None),
            raw_response=response,
        )

    def iter_stream_chunks(self, response: Iterable[Any]) -> Iterable[ModelStreamChunk]:
        for chunk in response:
            usage = getattr(chunk, "usage", None)
            if not getattr(chunk, "choices", None):
                if usage is not None:
                    yield ModelStreamChunk(raw_usage=usage, raw_chunk=chunk)
                continue

            choice = chunk.choices[0]
            delta = getattr(choice, "delta", None)
            if delta is None:
                if usage is not None:
                    yield ModelStreamChunk(raw_usage=usage, raw_chunk=chunk)
                continue

            yield ModelStreamChunk(
                content_delta=getattr(delta, "content", None) or "",
                reasoning_summary=self._extract_reasoning_summary(delta),
                tool_call_deltas=[
                    self._normalize_tool_call_delta(tool_call_delta)
                    for tool_call_delta in (getattr(delta, "tool_calls", None) or [])
                ],
                raw_usage=usage,
                raw_chunk=chunk,
            )

    def _normalize_tool_call(self, tool_call: Any) -> dict[str, Any]:
        function = self._get_field(tool_call, "function", {})
        return {
            "id": str(self._get_field(tool_call, "id", "")),
            "type": str(self._get_field(tool_call, "type", "function")),
            "function": {
                "name": str(self._get_field(function, "name", "")),
                "arguments": str(self._get_field(function, "arguments", "")),
            },
        }

    def _normalize_tool_call_delta(self, tool_call_delta: Any) -> ModelToolCallDelta:
        function = self._get_field(tool_call_delta, "function", None)
        return ModelToolCallDelta(
            index=int(self._get_field(tool_call_delta, "index", 0) or 0),
            tool_call_id=self._optional_str(self._get_field(tool_call_delta, "id", None)),
            name_delta=(
                self._optional_str(self._get_field(function, "name", None))
                if function is not None
                else ""
            )
            or "",
            arguments_delta=(
                self._optional_str(self._get_field(function, "arguments", None))
                if function is not None
                else ""
            )
            or "",
        )

    def _extract_reasoning_summary(self, delta: Any) -> str:
        for field_name in (
            "reasoning_summary",
            "reasoning_content",
            "reasoning",
            "thinking",
        ):
            value = getattr(delta, field_name, None)
            if value:
                return str(value)
        return ""

    def _raise_for_error_payload(self, response: Any) -> None:
        error = getattr(response, "error", None)
        if not isinstance(error, dict):
            return
        message = str(error.get("message") or "Unknown chat completion error")
        code = error.get("code")
        if code:
            message = f"{message} (code: {code})"
        raise RuntimeError(f"Chat completion failed: {message}")

    def _get_field(self, value: Any, field_name: str, default: Any = None) -> Any:
        if isinstance(value, dict):
            return value.get(field_name, default)
        return getattr(value, field_name, default)

    def _optional_str(self, value: Any) -> str | None:
        if value is None:
            return None
        return str(value)
