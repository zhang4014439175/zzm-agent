import json
import re
from dataclasses import dataclass
from time import perf_counter, sleep
from typing import TYPE_CHECKING, Any, Callable

from zzm_agent.core.errors import ToolError, tool_error_from_exception
from zzm_agent.core.observability import (
    TokenUsage,
    ToolEvent,
    ToolEventCallback,
    UsageState,
    tool_end_event,
    tool_error_event,
    tool_start_event,
)
from zzm_agent.core.progress_monitor import (
    ProgressMonitor,
    ProgressSignal,
    ToolObservation,
)
from zzm_agent.core.runtime_messages import ConversationMessageStore
from zzm_agent.core.runtime_state import LoopState, LoopTransition, TurnState
from zzm_agent.memory.token_counter import TokenCounter

if TYPE_CHECKING:
    from openai import OpenAI
    from zzm_agent.core.tool_registry import ToolRegistry
    from zzm_agent.memory.store import MemoryStore
    from zzm_agent.prompt.manager import PromptManager


@dataclass
class _ToolExecutionOutcome:
    """Internal result of one registry tool invocation."""

    content: str
    success: bool
    attempts: int = 1
    error: ToolError | None = None


_TEXT_TOOL_CALL_PATTERN = re.compile(
    r"<tool_call>\s*(?P<name>[A-Za-z_][\w.\-]*)\s*(?P<body>.*?)</tool_call>",
    re.DOTALL | re.IGNORECASE,
)
_TEXT_TOOL_ARG_PAIR_PATTERN = re.compile(
    r"<arg_key>\s*(?P<key>.*?)\s*</arg_key>\s*"
    r"<arg_value>\s*(?P<value>.*?)\s*</arg_value>",
    re.DOTALL | re.IGNORECASE,
)
_TEXT_TOOL_NAME_ALIASES = {
    "shell": "run_shell",
    "powershell": "run_shell",
    "bash": "run_shell",
    "cmd": "run_shell",
}
_TEXT_TOOL_ARG_ALIASES = {
    "run_shell": {
        "cmd": "command",
    }
}


class AgentLoop:
    """
    The main execution loop for the agent.
    
    It manages conversation history, handles tool calling loops, and
    interacts with the OpenAI-compatible API.
    """

    def __init__(
        self,
        client: "OpenAI",
        model: str,
        system_prompt: str,
        registry: "ToolRegistry",
        store: "MemoryStore",
        memory_injection_limit: int = 3,
        temperature: float | None = None,
        max_tokens: int | None = None,
        auto_approve: bool = False,
        safe_mode: bool = False,
        confirm_tool: Callable[[str, dict[str, Any], str], bool] | None = None,
        max_tool_iterations: int = 20,
        duplicate_tool_call_limit: int = 3,
        max_tool_retries: int = 1,
        retry_base_delay: float = 0.25,
        retry_max_delay: float = 5.0,
        retry_sleep: Callable[[float], None] | None = None,
        tool_choice: str | None = "auto",
        on_tool_start: ToolEventCallback | None = None,
        on_tool_end: ToolEventCallback | None = None,
        on_tool_error: ToolEventCallback | None = None,
        prompt_manager: "PromptManager | None" = None,
    ):
        """
        Initialize the AgentLoop.
        
        Args:
            client: An OpenAI client instance.
            model: The name of the model to use (e.g., 'gpt-4').
            system_prompt: The initial system instructions for the agent.
            registry: The tool registry containing available functions.
            store: The memory store for persisting history.
            max_tool_iterations: Maximum model tool-call rounds before stopping.
            duplicate_tool_call_limit: Consecutive identical calls before stopping.
            max_tool_retries: Automatic retries for retryable tool execution errors.
            retry_base_delay: First automatic retry delay in seconds.
            retry_max_delay: Maximum automatic retry delay in seconds.
            retry_sleep: Optional sleep function used by retry backoff. Primarily
                useful for tests that should not actually sleep.
            tool_choice: Tool-choice policy to send when tools are present. Set
                to None for providers that reject the field.
            on_tool_start: Callback for structured tool start events.
            on_tool_end: Callback for successful or denied tool completion events.
            on_tool_error: Callback for failed tool completion events.
            prompt_manager: Optional dynamic prompt composer for per-turn system prompts.
        """
        self.client = client
        self.model = model
        self.system_prompt = system_prompt
        self.registry = registry
        self.store = store
        self.memory_injection_limit = memory_injection_limit
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.auto_approve = auto_approve
        self.safe_mode = safe_mode
        self.confirm_tool = confirm_tool
        self.max_tool_iterations = max(1, max_tool_iterations)
        self.duplicate_tool_call_limit = max(1, duplicate_tool_call_limit)
        self.max_tool_retries = max(0, max_tool_retries)
        self.retry_base_delay = max(0.0, retry_base_delay)
        self.retry_max_delay = max(self.retry_base_delay, retry_max_delay)
        self.retry_sleep = retry_sleep or sleep
        self.tool_choice = tool_choice
        self._tool_choice_disabled_by_provider = False
        self.on_tool_start = on_tool_start
        self.on_tool_end = on_tool_end
        self.on_tool_error = on_tool_error
        self.prompt_manager = prompt_manager
        self.last_turn_usage = TokenUsage()
        self.cumulative_usage = TokenUsage()
        self.usage_state = UsageState()
        self.last_context_window: dict[str, Any] = {}
        self.last_progress_signal: ProgressSignal | None = None
        self.last_reflection_count = 0
        self.last_turn_state: TurnState | None = None
        self.last_loop_state: LoopState | None = None
        self.last_message_store: ConversationMessageStore | None = None
        self.token_counter = TokenCounter(model=model)

    def _build_tool_call_record(
        self,
        tool_call_id: str,
        name: str,
        arguments: str,
    ) -> dict[str, Any]:
        return {
            "id": tool_call_id,
            "type": "function",
            "function": {
                "name": name,
                "arguments": arguments,
            },
        }

    def _extract_tool_calls(self, tool_calls: list[Any]) -> list[dict[str, Any]]:
        # Non-stream responses already carry complete tool calls, so this path
        # just normalizes SDK objects into the persisted assistant message shape.
        records = []
        for tc in tool_calls:
            records.append(
                self._build_tool_call_record(
                    tool_call_id=tc.id,
                    name=tc.function.name,
                    arguments=tc.function.arguments,
                )
            )
        return records

    def _resolve_text_tool_name(self, name: str) -> str | None:
        """Map text-emitted tool names to registered native tool names."""
        normalized = name.strip()
        alias = _TEXT_TOOL_NAME_ALIASES.get(normalized.lower())
        if alias is not None:
            return alias
        if normalized in self.registry.tools:
            return normalized
        return None

    def _normalize_text_tool_arguments(
        self,
        tool_name: str,
        arguments: dict[str, str],
    ) -> dict[str, Any]:
        """Map text-emitted argument names to the registered tool schema."""
        aliases = _TEXT_TOOL_ARG_ALIASES.get(tool_name, {})
        normalized: dict[str, Any] = {}
        for key, value in arguments.items():
            normalized_key = aliases.get(key.strip(), key.strip())
            normalized[normalized_key] = value.strip()
        return normalized

    def _extract_text_tool_calls(self, content: str) -> list[dict[str, Any]]:
        """Parse model-emitted pseudo tool calls from assistant text.

        This is a compatibility fallback for providers/models that ignore native
        OpenAI tool calling and instead print a tool call as text.
        """
        if "<tool_call>" not in content.lower():
            return []

        records: list[dict[str, Any]] = []
        for index, match in enumerate(_TEXT_TOOL_CALL_PATTERN.finditer(content), start=1):
            tool_name = self._resolve_text_tool_name(match.group("name"))
            if tool_name is None:
                continue

            raw_arguments: dict[str, str] = {}
            for arg_match in _TEXT_TOOL_ARG_PAIR_PATTERN.finditer(match.group("body")):
                key = arg_match.group("key").strip()
                if not key:
                    continue
                raw_arguments[key] = arg_match.group("value").strip()

            arguments = self._normalize_text_tool_arguments(tool_name, raw_arguments)
            records.append(
                self._build_tool_call_record(
                    tool_call_id=f"text_call_{index}",
                    name=tool_name,
                    arguments=json.dumps(arguments, ensure_ascii=False),
                )
            )
        return records

    def _text_tool_call_start(self, text: str, tools: list[dict[str, Any]]) -> int:
        """Return the first pseudo tool-call/candidate start index, or -1."""
        lowered = text.lower()
        marker = "<tool_call"
        marker_index = lowered.find(marker)
        if marker_index >= 0:
            return marker_index

        # Streamed text can split "<tool_call" across chunks. If the current
        # suffix is a prefix of the marker, keep it buffered until the next chunk
        # proves whether it is a pseudo tool call or normal text.
        start = max(0, len(lowered) - len(marker) + 1)
        for index in range(start, len(lowered)):
            suffix = lowered[index:]
            if suffix and marker.startswith(suffix):
                return index
        return -1

    def _usage_from_sdk_object(self, usage: Any) -> TokenUsage:
        """Normalize OpenAI-compatible usage metadata when the SDK provides it."""
        if usage is None:
            return TokenUsage()

        def get_int(field: str) -> int:
            if isinstance(usage, dict):
                value = usage.get(field, 0)
            else:
                value = getattr(usage, field, 0)
            if not isinstance(value, (int, float, str)):
                return 0
            try:
                return int(value or 0)
            except (TypeError, ValueError):
                return 0

        def get_nested_int(parent_field: str, field: str) -> int:
            if isinstance(usage, dict):
                parent = usage.get(parent_field, {})
            else:
                parent = getattr(usage, parent_field, {})
            if parent is None:
                return 0
            if isinstance(parent, dict):
                value = parent.get(field, 0)
            else:
                value = getattr(parent, field, 0)
            try:
                return int(value or 0)
            except (TypeError, ValueError):
                return 0

        prompt_tokens = get_int("prompt_tokens")
        completion_tokens = get_int("completion_tokens")
        total_tokens = get_int("total_tokens") or prompt_tokens + completion_tokens
        if total_tokens <= 0 and prompt_tokens <= 0 and completion_tokens <= 0:
            return TokenUsage()
        return TokenUsage(
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=total_tokens,
            cache_creation_tokens=(
                get_int("cache_creation_input_tokens")
                or get_nested_int("prompt_tokens_details", "cache_creation_tokens")
            ),
            cache_read_tokens=(
                get_int("cache_read_input_tokens")
                or get_nested_int("prompt_tokens_details", "cached_tokens")
                or get_nested_int("prompt_tokens_details", "cache_read_tokens")
            ),
            reasoning_tokens=get_nested_int(
                "completion_tokens_details",
                "reasoning_tokens",
            ),
            source="api",
        )

    def _estimate_text_tokens(self, text: str) -> int:
        """Estimate token count with the E5 tokenizer fallback chain."""
        return self.token_counter.count_text(text)

    def _estimate_messages_tokens(self, messages: list[dict[str, Any]]) -> int:
        serialized = json.dumps(messages, ensure_ascii=False, default=str)
        return self._estimate_text_tokens(serialized)

    def _estimate_call_usage(
        self,
        messages: list[dict[str, Any]],
        assistant_content: str,
        tool_calls: list[dict[str, Any]],
    ) -> TokenUsage:
        """Estimate usage when the provider omits token accounting."""
        completion_payload = assistant_content
        if tool_calls:
            completion_payload += json.dumps(tool_calls, ensure_ascii=False, default=str)
        prompt_tokens = self._estimate_messages_tokens(messages)
        completion_tokens = self._estimate_text_tokens(completion_payload)
        return TokenUsage(
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=prompt_tokens + completion_tokens,
            source="estimated",
        )

    def _apply_tool_kwargs(
        self,
        kwargs: dict[str, Any],
        tools: list[dict[str, Any]],
    ) -> None:
        """Attach tool schemas and compatible tool_choice policy to request kwargs."""
        if not tools:
            return
        kwargs["tools"] = tools
        if self.tool_choice and not self._tool_choice_disabled_by_provider:
            kwargs["tool_choice"] = self.tool_choice

    def _chat_completion_kwargs(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        stream: bool,
    ) -> dict[str, Any]:
        """Build the provider request payload used for one model call."""
        kwargs: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
        }
        if stream:
            kwargs["stream"] = True
        if self.temperature is not None:
            kwargs["temperature"] = self.temperature
        if self.max_tokens is not None:
            kwargs["max_tokens"] = self.max_tokens
        self._apply_tool_kwargs(kwargs, tools)
        return kwargs

    def _save_context_snapshot(
        self,
        *,
        user_input: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        stream: bool,
        tool_iteration: int,
    ) -> None:
        """Write the latest complete prompt payload into the active session."""
        kwargs = self._chat_completion_kwargs(messages, tools, stream=stream)
        self.store.save_latest_context(
            {
                "model": self.model,
                "latest_user_input": user_input,
                "stream": stream,
                "tool_iteration": tool_iteration,
                "context_window": self.last_context_window,
                "request": kwargs,
            }
        )

    def _provider_rejects_tool_choice(self, exc: Exception) -> bool:
        """Return whether a provider error indicates unsupported tool_choice."""
        text = str(exc).lower()
        return "tool_choice" in text and (
            "no endpoints found" in text
            or "unsupported" in text
            or "not support" in text
            or "does not support" in text
        )

    def _provider_rejects_streaming(self, exc: Exception) -> bool:
        """Return whether a provider error looks specific to streaming/upstream transport."""
        text = str(exc).lower()
        return (
            "bad_response_status_code" in text
            or "upstream service temporarily unavailable" in text
            or ("openai_error" in text and "stream" in text)
        )

    def _retry_without_tool_choice(
        self,
        exc: Exception,
        kwargs: dict[str, Any],
    ) -> Any:
        """Retry a request once without tool_choice when a provider rejects it."""
        if "tool_choice" not in kwargs or not self._provider_rejects_tool_choice(exc):
            raise exc
        retry_kwargs = dict(kwargs)
        retry_kwargs.pop("tool_choice", None)
        self._tool_choice_disabled_by_provider = True
        return self.client.chat.completions.create(**retry_kwargs)

    def _raise_for_api_error_response(self, response: Any) -> None:
        """Raise a useful exception when a provider returns an error payload."""
        error = getattr(response, "error", None)
        if not isinstance(error, dict):
            return

        message = str(error.get("message") or "Unknown chat completion error")
        code = error.get("code")
        if code:
            message = f"{message} (code: {code})"
        raise RuntimeError(f"Chat completion failed: {message}")

    def _first_choice_message(self, response: Any) -> Any:
        """Extract the first response message, validating malformed payloads."""
        self._raise_for_api_error_response(response)
        choices = getattr(response, "choices", None)
        if not choices:
            raise RuntimeError("Chat completion failed: response did not include choices.")
        message = getattr(choices[0], "message", None)
        if message is None:
            raise RuntimeError("Chat completion failed: response choice did not include a message.")
        return message

    def _complete_once(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
    ) -> tuple[str, list[dict[str, Any]], bool, TokenUsage]:
        kwargs = self._chat_completion_kwargs(messages, tools, stream=False)

        try:
            response = self.client.chat.completions.create(**kwargs)
        except Exception as exc:
            response = self._retry_without_tool_choice(exc, kwargs)
        msg = self._first_choice_message(response)
        content = msg.content or ""
        tool_calls = self._extract_tool_calls(msg.tool_calls or [])
        if not tool_calls:
            tool_calls = self._extract_text_tool_calls(content)
            if tool_calls:
                content = ""
        return (
            content,
            tool_calls,
            False,
            self._usage_from_sdk_object(getattr(response, "usage", None)),
        )

    def _stream_once(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        on_text_chunk: Callable[[str], None] | None,
    ) -> tuple[str, list[dict[str, Any]], bool, TokenUsage]:
        kwargs = self._chat_completion_kwargs(messages, tools, stream=True)

        text_parts: list[str] = []
        emitted_text_length = 0
        usage = TokenUsage()
        # Streamed tool calls are incremental: the model can emit the same call
        # over multiple chunks, so we rebuild each call by its stable index.
        tool_call_map: dict[int, dict[str, Any]] = {}

        try:
            try:
                response = self.client.chat.completions.create(**kwargs)
            except Exception as exc:
                try:
                    response = self._retry_without_tool_choice(exc, kwargs)
                except Exception as retry_exc:
                    if self._provider_rejects_streaming(retry_exc):
                        return self._complete_once(messages=messages, tools=tools)
                    raise
            for chunk in response:
                chunk_usage = self._usage_from_sdk_object(getattr(chunk, "usage", None))
                if chunk_usage.has_tokens():
                    usage.add(chunk_usage)

                # Ignore keep-alive/empty chunks that carry no choice payload.
                if not getattr(chunk, "choices", None):
                    continue

                choice = chunk.choices[0]
                delta = getattr(choice, "delta", None)
                # Only delta chunks contain incremental streamed content.
                if delta is None:
                    continue

                content = getattr(delta, "content", None)
                if content:
                    # Preserve the full assistant reply locally and optionally
                    # push each fragment to the caller for real-time rendering.
                    text_parts.append(content)
                    if on_text_chunk is not None:
                        full_text = "".join(text_parts)
                        tool_call_start = self._text_tool_call_start(full_text, tools)
                        if tool_call_start >= 0:
                            if emitted_text_length < tool_call_start:
                                on_text_chunk(full_text[emitted_text_length:tool_call_start])
                                emitted_text_length = tool_call_start
                        else:
                            on_text_chunk(full_text[emitted_text_length:])
                            emitted_text_length = len(full_text)

                for tc_delta in getattr(delta, "tool_calls", None) or []:
                    index = getattr(tc_delta, "index", 0)
                    # Create the accumulator the first time this tool-call slot appears.
                    record = tool_call_map.setdefault(
                        index,
                        self._build_tool_call_record("", "", ""),
                    )

                    if getattr(tc_delta, "id", None):
                        # The id may show up after earlier fragments, so keep refreshing it.
                        record["id"] = tc_delta.id

                    function = getattr(tc_delta, "function", None)
                    if function is None:
                        continue

                    # Function metadata can arrive piece by piece; concatenate the
                    # fragments until we have the full callable name and JSON args.
                    if getattr(function, "name", None):
                        record["function"]["name"] += function.name
                    if getattr(function, "arguments", None):
                        record["function"]["arguments"] += function.arguments
        except (KeyboardInterrupt, GeneratorExit):
            # Let the caller keep any text already shown to the user, but signal
            # that this turn was interrupted so partial tool state is discarded.
            return "".join(text_parts), [], True, usage
        except Exception:
            if text_parts:
                # If the transport dies mid-stream after visible output, treat it
                # like an interrupted response instead of raising after partial render.
                return "".join(text_parts), [], True, usage
            raise

        # Tool calls must be replayed in their original order for downstream execution.
        tool_calls = [tool_call_map[i] for i in sorted(tool_call_map)]
        content = "".join(text_parts)
        if not tool_calls:
            tool_calls = self._extract_text_tool_calls(content)
            if tool_calls:
                return "", tool_calls, False, usage
        if on_text_chunk is not None and emitted_text_length < len(content):
            on_text_chunk(content[emitted_text_length:])
        return content, tool_calls, False, usage

    def _requires_tool_confirmation(self, risk_level: str) -> bool:
        """Return whether a tool call needs interactive approval."""
        if self.auto_approve:
            return False
        if risk_level in {"medium", "high"}:
            return True
        return False

    def _is_tool_execution_approved(self, name: str, arguments: dict[str, Any]) -> bool:
        """Check whether a requested tool call is allowed to execute."""
        risk_level = self.registry.get_tool_meta(name).get("risk_level", "low")
        if not self._requires_tool_confirmation(risk_level):
            return True
        if self.confirm_tool is None:
            return False
        return bool(self.confirm_tool(name, arguments, risk_level))

    def _tool_call_signature(self, tool_call: dict[str, Any]) -> tuple[str, str]:
        """Return a stable signature used to detect repeated tool calls."""
        function = tool_call.get("function", {})
        name = str(function.get("name", ""))
        arguments = str(function.get("arguments", ""))
        try:
            parsed = json.loads(arguments)
        except json.JSONDecodeError:
            normalized_args = arguments
        else:
            normalized_args = json.dumps(parsed, sort_keys=True, ensure_ascii=False)
        return name, normalized_args

    def _repetition_stop_message(self, signature: tuple[str, str]) -> str:
        """Build the final response when the model repeats the same tool blindly."""
        name, arguments = signature
        return (
            "Stopped tool execution because the model repeatedly requested the "
            f"same tool call: {name}({arguments}). Please adjust the approach "
            "or provide more specific input."
        )

    def _iteration_stop_message(self) -> str:
        """Build the final response when the tool loop reaches its safety limit."""
        return (
            "Stopped tool execution because the maximum tool iteration limit "
            f"({self.max_tool_iterations}) was reached. Please narrow the task "
            "or continue with a more specific instruction."
        )

    def _format_retried_error(self, error: ToolError, attempts: int) -> str:
        """Annotate the final retryable error with retry context."""
        error.attempts = attempts
        if attempts <= 1:
            error.recovery_hint = f"{error.recovery_hint} {error.retry_summary()}"
            return error.to_json()
        retries = attempts - 1
        error.recovery_hint = (
            f"{error.recovery_hint} {error.retry_summary()} "
            f"Automatic retry exhausted after {retries} retry attempt(s)."
        )
        return error.to_json()

    def _retry_delay_for_error(self, error: ToolError, retry_index: int) -> float:
        """Return the bounded delay before the next automatic retry."""
        if error.retry_after_seconds is not None:
            return min(self.retry_max_delay, max(0.0, error.retry_after_seconds))
        return min(self.retry_max_delay, self.retry_base_delay * (2 ** retry_index))

    def _execute_tool_with_retries(self, name: str, args: dict[str, Any]) -> _ToolExecutionOutcome:
        """Execute a tool with bounded retries for structured retryable errors."""
        attempts = 0
        last_error: ToolError | None = None
        max_attempts = self.max_tool_retries + 1

        while attempts < max_attempts:
            attempts += 1
            try:
                return _ToolExecutionOutcome(
                    content=str(self.registry.call(name, args)),
                    success=True,
                    attempts=attempts,
                )
            except Exception as exc:
                last_error = tool_error_from_exception(exc)
                if not last_error.retryable or attempts >= max_attempts:
                    return _ToolExecutionOutcome(
                        content=self._format_retried_error(last_error, attempts),
                        success=False,
                        attempts=attempts,
                        error=last_error,
                    )
                delay = self._retry_delay_for_error(last_error, attempts - 1)
                if delay > 0:
                    self.retry_sleep(delay)

        # This is defensive; the loop always returns on success or final error.
        if last_error is None:
            last_error = ToolError(
                error_type="ToolExecutionError",
                message="Tool execution failed without an exception payload.",
                recovery_hint="Inspect tool execution logs before retrying.",
                retryable=False,
            )
            return _ToolExecutionOutcome(
                content=last_error.to_json(),
                success=False,
                attempts=attempts,
                error=last_error,
            )
        return _ToolExecutionOutcome(
            content=self._format_retried_error(last_error, attempts),
            success=False,
            attempts=attempts,
            error=last_error,
        )

    def _emit_tool_event(
        self,
        callback: ToolEventCallback | None,
        event: ToolEvent,
    ) -> None:
        """Send a tool event to observers without letting UI/logging break the loop."""
        if callback is None:
            return
        try:
            callback(event)
        except Exception:
            # Observability must never change agent behavior.
            return

    def _commit_turn_usage(self, usage: TokenUsage) -> None:
        """Persist the latest turn and cumulative model usage counters."""
        self.last_turn_usage = usage.copy()
        self.cumulative_usage.add(usage)
        if self.last_turn_state is not None:
            self.last_turn_state.usage = self.last_turn_usage.copy()
            self.last_turn_state.usage_state.turn = self.last_turn_usage.copy()
        save_usage_state = getattr(self.store, "save_usage_state", None)
        if callable(save_usage_state):
            save_usage_state(self.usage_state)

    def _build_system_prompt(self, user_input: str) -> str:
        """Return the static or dynamically assembled system prompt for this turn."""
        if self.prompt_manager is None:
            return self.system_prompt
        history = self.store.load_history()
        return self.prompt_manager.build(user_input=user_input, history=history)

    def _no_progress_stop_message(
        self,
        signal: ProgressSignal,
        *,
        after_reflection: bool = False,
    ) -> str:
        """Build the final response when completed tool rounds make no progress."""
        reflection_context = " after reflection" if after_reflection else ""
        return (
            f"Stopped tool execution because no progress was detected{reflection_context} "
            f"({signal.reason}) after {signal.round_count} tool round(s). "
            f"{signal.detail}"
        )

    def _reflection_prompt(self, signal: ProgressSignal) -> str:
        """Build a bounded runtime-only prompt that asks the model to change approach."""
        return (
            "[REFLECTION_REQUIRED]\n"
            "The recent tool execution is not making progress.\n"
            f"Reason: {signal.reason}\n"
            f"Observed tool rounds: {signal.round_count}\n"
            f"Details: {signal.detail}\n\n"
            "Before taking another action:\n"
            "1. Briefly reassess why the previous approach failed.\n"
            "2. Do not repeat the same tool call or an equivalent call that yields "
            "the same observation.\n"
            "3. Choose a materially different tool, arguments, or strategy.\n"
            "4. If progress requires missing user input, permission, credentials, "
            "or an unavailable external condition, stop using tools and report the "
            "blocker clearly.\n"
            "This is the only reflection retry available for this turn."
        )

    def _request_reflection(
        self,
        message_store: ConversationMessageStore,
        signal: ProgressSignal,
    ) -> bool:
        """Inject one runtime-only reflection prompt and report whether it was added."""
        self.last_progress_signal = signal
        if self.last_loop_state is not None:
            self.last_loop_state.record_progress_signal(signal)
        if self.last_reflection_count >= 1:
            return False
        message_store.append_runtime_only(
            {"role": "system", "content": self._reflection_prompt(signal)}
        )
        if self.last_loop_state is not None:
            self.last_loop_state.record_reflection(signal)
            self.last_reflection_count = self.last_loop_state.reflection_count
        else:
            self.last_reflection_count += 1
        return True

    def run(
        self,
        user_input: str,
        stream: bool = True,
        on_text_chunk: Callable[[str], None] | None = None,
    ) -> str:
        """
        Run a single turn of the conversation.
        
        This method will load history, call the LLM, execute any requested
        tools in a loop, and finally return the model's text response.
        
        Args:
            user_input: The message from the user.
            stream: Whether to request a streaming response from the model.
            on_text_chunk: Optional callback invoked for every streamed text chunk.
            
        Returns:
            The final text response from the assistant.
        """
        # 1. Load runtime context: base instructions + long-term memory +
        # compressed session history + current user input.
        system_prompt = self._build_system_prompt(user_input)
        messages, _compression = self.store.build_turn_messages(
            system_prompt=system_prompt,
            user_input=user_input,
            memory_limit=self.memory_injection_limit,
        )
        self.last_context_window = _compression

        user_message = {"role": "user", "content": user_input}
        message_store = ConversationMessageStore.begin_turn(
            persisted_messages=self.store.load_history(),
            model_context_messages=messages,
            user_message=user_message,
        )
        self.last_message_store = message_store
        turn_usage = TokenUsage()
        turn_state = TurnState(user_input=user_input)
        turn_state.start()
        loop_state = turn_state.start_loop()
        self.last_turn_state = turn_state
        self.last_loop_state = loop_state
        session_id = getattr(self.store, "session_id", None)
        load_usage_state = getattr(self.store, "load_usage_state", None)
        if callable(load_usage_state):
            self.usage_state = load_usage_state()
        self.usage_state.start_turn(turn_state.turn_id)
        self.usage_state.set_conversation(session_id)
        turn_state.usage_state = self.usage_state
        
        # Get available tool schemas
        tools = self.registry.get_schemas()
        tool_schema_tokens = self._estimate_text_tokens(
            json.dumps(tools, ensure_ascii=False, sort_keys=True, default=str)
        ) if tools else 0
        message_tokens = int(self.last_context_window.get("total_tokens", 0) or 0)
        self.last_context_window = {
            **self.last_context_window,
            "message_tokens": message_tokens,
            "tool_schema_tokens": tool_schema_tokens,
            "total_tokens": message_tokens + tool_schema_tokens,
        }

        # 2. Start the thinking-action loop
        # print(messages)
        tool_iterations = 0
        consecutive_signature: tuple[str, str] | None = None
        consecutive_count = 0
        progress_monitor = ProgressMonitor()
        self.last_progress_signal = None
        self.last_reflection_count = 0
        while True:
            if tool_iterations >= self.max_tool_iterations:
                final_reply = self._iteration_stop_message()
                turn_state.block(final_reply, reason=LoopTransition.ITERATION_LIMIT)
                assistant_msg = {"role": "assistant", "content": final_reply}
                message_store.append_pending(assistant_msg)
                message_store.commit(self.store.append)
                self._commit_turn_usage(turn_usage)
                return final_reply

            model_context_messages = message_store.prepare_model_context()
            self._save_context_snapshot(
                user_input=user_input,
                messages=model_context_messages,
                tools=tools,
                stream=stream,
                tool_iteration=tool_iterations,
            )

            if stream:
                loop_state.record_model_call()
                loop_state.record_streaming_response()
                assistant_content, tool_calls_raw, interrupted, call_usage = self._stream_once(
                    messages=model_context_messages,
                    tools=tools,
                    on_text_chunk=on_text_chunk,
                )
            else:
                loop_state.record_model_call()
                assistant_content, tool_calls_raw, interrupted, call_usage = self._complete_once(
                    messages=model_context_messages,
                    tools=tools,
                )

            if not call_usage.has_tokens():
                call_usage = self._estimate_call_usage(
                    messages=model_context_messages,
                    assistant_content=assistant_content,
                    tool_calls=tool_calls_raw,
                )
            accounted_call_usage = self.usage_state.record_model_call(
                call_usage,
                model=self.model,
                tool_schema_tokens=tool_schema_tokens,
            )
            turn_usage.add(accounted_call_usage)

            if interrupted:
                # Interrupted turns intentionally do not write partial assistant
                # or tool state, preserving a resumable conversation history.
                turn_state.cancel("interrupted")
                message_store.rollback_pending()
                self._commit_turn_usage(turn_usage)
                return assistant_content

            # If it's a simple text reply, we are done
            if not tool_calls_raw:
                final_reply = assistant_content
                assistant_msg = {"role": "assistant", "content": final_reply}
                message_store.append_pending(assistant_msg)

                # Only the final assistant reply marks the turn as complete.
                message_store.commit(self.store.append)
                turn_state.complete(final_reply, usage=turn_usage)
                self._commit_turn_usage(turn_usage)
                return final_reply

            # If the model wants to call tools
            loop_state.validate_tool_calls(tool_calls_raw)
            tool_count_usage = self.usage_state.record_tool_calls(len(tool_calls_raw))
            turn_usage.add(tool_count_usage)
            current_signatures = [self._tool_call_signature(tc) for tc in tool_calls_raw]
            if len(current_signatures) == 1 and current_signatures[0] == consecutive_signature:
                consecutive_count += 1
            elif len(current_signatures) == 1:
                consecutive_signature = current_signatures[0]
                consecutive_count = 1
            else:
                consecutive_signature = None
                consecutive_count = 0

            if (
                consecutive_signature is not None
                and consecutive_count >= self.duplicate_tool_call_limit
            ):
                name, arguments = consecutive_signature
                repetition_signal = ProgressSignal(
                    reason="repeated_tool_call",
                    round_count=tool_iterations,
                    detail=(
                        "The model repeatedly requested the same tool call: "
                        f"{name}({arguments})."
                    ),
                )
                if self._request_reflection(message_store, repetition_signal):
                    continue
                final_reply = self._no_progress_stop_message(
                    repetition_signal,
                    after_reflection=True,
                )
                turn_state.block(
                    final_reply,
                    reason=LoopTransition.DUPLICATE_CALL_LIMIT,
                )
                assistant_msg = {"role": "assistant", "content": final_reply}
                message_store.append_pending(assistant_msg)
                message_store.commit(self.store.append)
                self._commit_turn_usage(turn_usage)
                return final_reply

            tool_iterations += 1

            # Record the assistant's intent to call tools
            assistant_intent_msg = {
                "role": "assistant",
                "content": assistant_content or None,  # Use None if empty for tool calls
                "tool_calls": tool_calls_raw,
            }
            message_store.append_pending(assistant_intent_msg)

            # Tool results stay inside the same turn so the model can immediately
            # consume them on the next loop iteration.
            round_observations: list[ToolObservation] = []
            for tc in tool_calls_raw:
                name = ""
                args: dict[str, Any] = {}
                risk_level = "unknown"
                observation_success = False
                observation_retryable = False
                started_at = perf_counter()
                try:
                    # Parse arguments and call the tool through the registry
                    name = tc["function"]["name"]
                    args = json.loads(tc["function"]["arguments"])
                    risk_level = self.registry.get_tool_meta(name).get("risk_level", "low")
                    if self._requires_tool_confirmation(risk_level):
                        loop_state.await_permission()
                    if not self._is_tool_execution_approved(name, args):
                        loop_state.record_permission_denial()
                        result_str = "User denied tool execution."
                        duration_ms = (perf_counter() - started_at) * 1000
                        self._emit_tool_event(
                            self.on_tool_end,
                            tool_end_event(
                                tool_name=name,
                                tool_call_id=tc["id"],
                                arguments=args,
                                risk_level=risk_level,
                                status="denied",
                                duration_ms=duration_ms,
                                result=result_str,
                                attempts=0,
                            ),
                        )
                    else:
                        loop_state.record_tool_execution_start(tool_calls_raw)
                        self._emit_tool_event(
                            self.on_tool_start,
                            tool_start_event(
                                tool_name=name,
                                tool_call_id=tc["id"],
                                arguments=args,
                                risk_level=risk_level,
                            ),
                        )
                        outcome = self._execute_tool_with_retries(name, args)
                        result_str = outcome.content
                        duration_ms = (perf_counter() - started_at) * 1000
                        if outcome.success:
                            observation_success = True
                            self._emit_tool_event(
                                self.on_tool_end,
                                tool_end_event(
                                    tool_name=name,
                                    tool_call_id=tc["id"],
                                    arguments=args,
                                    risk_level=risk_level,
                                    status="success",
                                    duration_ms=duration_ms,
                                    result=result_str,
                                    attempts=outcome.attempts,
                                ),
                            )
                        else:
                            error = outcome.error or ToolError(
                                error_type="ToolExecutionError",
                                message=result_str,
                                recovery_hint="Inspect tool execution logs before retrying.",
                                retryable=False,
                            )
                            observation_retryable = error.retryable
                            self._emit_tool_event(
                                self.on_tool_error,
                                tool_error_event(
                                    tool_name=name,
                                    tool_call_id=tc["id"],
                                    arguments=args,
                                    risk_level=risk_level,
                                    duration_ms=duration_ms,
                                    error_type=error.error_type,
                                    error_message=error.message,
                                    attempts=outcome.attempts,
                                    result=result_str,
                                ),
                            )
                except Exception as e:
                    # Capture tool execution errors and feed them back to the model
                    error = tool_error_from_exception(e)
                    observation_retryable = error.retryable
                    result_str = error.to_json()
                    duration_ms = (perf_counter() - started_at) * 1000
                    self._emit_tool_event(
                        self.on_tool_error,
                        tool_error_event(
                            tool_name=name or "<unknown>",
                            tool_call_id=tc.get("id", ""),
                            arguments=args,
                            risk_level=risk_level,
                            duration_ms=duration_ms,
                            error_type=error.error_type,
                            error_message=error.message,
                            attempts=1,
                            result=result_str,
                        ),
                    )

                tool_result_msg = {
                    "role": "tool",
                    "tool_call_id": tc["id"],
                    "content": result_str,
                }
                message_store.append_pending(tool_result_msg)
                round_observations.append(
                    ToolObservation(
                        tool_name=name or "<unknown>",
                        arguments=tc.get("function", {}).get("arguments", ""),
                        content=result_str,
                        success=observation_success,
                        retryable=observation_retryable,
                    )
                )

            loop_state.record_tool_round(tool_calls_raw, round_observations)
            progress_signal = progress_monitor.observe_round(round_observations)
            if progress_signal is not None:
                if self._request_reflection(message_store, progress_signal):
                    continue
                final_reply = self._no_progress_stop_message(
                    progress_signal,
                    after_reflection=True,
                )
                turn_state.block(final_reply, reason=progress_signal.reason)
                assistant_msg = {"role": "assistant", "content": final_reply}
                message_store.append_pending(assistant_msg)
                message_store.commit(self.store.append)
                self._commit_turn_usage(turn_usage)
                return final_reply
            
            # Continue the loop to let the model process tool results
