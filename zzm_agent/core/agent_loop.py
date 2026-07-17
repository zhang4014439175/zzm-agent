import json
import re
from time import perf_counter, sleep
from typing import TYPE_CHECKING, Any, Callable

from zzm_agent.core.errors import ToolError, tool_error_from_exception
from zzm_agent.core.hooks import (
    HookContext,
    HookDecision,
    HookRegistry,
    HookResult,
    HookType,
)
from zzm_agent.core.model_adapter import OpenAIChatCompletionsAdapter
from zzm_agent.core.context_preparation import ContextPreparationService
from zzm_agent.core.model_turn import ModelTurnDriver
from zzm_agent.core.model_stream import ModelStreamEvent
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
from zzm_agent.core.recovery_policy import RecoveryPolicy
from zzm_agent.core.runtime_messages import ConversationMessageStore
from zzm_agent.core.runtime_records import ArtifactStore
from zzm_agent.core.segments import SegmentResult
from zzm_agent.core.state import (
    CancellationController,
    CancellationError,
    CancellationToken,
    LoopState,
    LoopTransition,
    PermissionState,
    TurnState,
    TurnStatus,
)
from zzm_agent.core.tool_results import ToolResult
from zzm_agent.core.tool_coordinator import ToolCallCoordinator, ToolExecutionOutcome
from zzm_agent.memory.token_counter import TokenCounter

if TYPE_CHECKING:
    from openai import OpenAI
    from zzm_agent.core.model_adapter import ModelStreamChunk
    from zzm_agent.core.tool_registry import ToolRegistry
    from zzm_agent.memory.store import MemoryStore
    from zzm_agent.prompt.manager import PromptManager


_TEXT_TOOL_CALL_PATTERN = re.compile(
    r"<tool_call(?::\w+)?>\s*(?:\s*<tool_sep(?::\w+)?>)?\s*(?P<name>[A-Za-z_][\w.\-]*)\s*(?:\s*<tool_sep(?::\w+)?>)?\s*(?P<body>.*?)</tool_call(?::\w+)?>",
    re.DOTALL | re.IGNORECASE,
)
_TEXT_TOOL_ARG_PAIR_PATTERN = re.compile(
    r"<arg_key(?::\w+)?>\s*(?P<key>.*?)\s*</arg_key(?::\w+)?>\s*"
    r"<arg_value(?::\w+)?>\s*(?P<value>.*?)\s*</arg_value(?::\w+)?>",
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
        cancellation_controller: CancellationController | None = None,
        hook_registry: HookRegistry | None = None,
        max_stop_hook_attempts: int = 1,
        empty_final_retries: int = 2,
        max_inline_tool_result_tokens: int = 2000,
        artifact_store: ArtifactStore | None = None,
        model_adapter: OpenAIChatCompletionsAdapter | None = None,
    ):
        """初始化单个执行段使用的 Agent 运行循环。

        AgentLoop 负责一次 Segment 内的模型调用、工具执行、权限检查、失败恢复、
        Token 统计与状态迁移；跨 Segment 的自动续跑由 QueryEngine 负责。这样达到
        ``max_tool_iterations`` 时只会安全让出，不会把整个用户任务误判为结束。

        关键参数：
            client/model: OpenAI 兼容客户端与模型名称。
            system_prompt/registry/store: 系统指令、可用工具目录和会话存储。
            max_tool_iterations: 单个 Segment 允许的工具轮次；达到后生成检查点。
            duplicate_tool_call_limit: 连续重复调用的熔断阈值，用于识别无进展循环。
            max_tool_retries: 可恢复工具错误在同一次调用中的自动重试次数。
            retry_base_delay/retry_max_delay: 指数退避的初始与最大等待时间。
            retry_sleep: 可替换的等待函数，测试中可避免真实休眠。
            tool_choice: Provider 接受的工具选择策略；不兼容时可设为 ``None``。
            on_tool_start/on_tool_end/on_tool_error: 工具生命周期事件回调。
            prompt_manager: 按当前任务和历史动态组装系统提示词的可选组件。
            cancellation_controller: 负责 Turn、Task 和工具调用取消传播的控制器。
            hook_registry/max_stop_hook_attempts: 生命周期 Hook 及停止检查重试上限。
            empty_final_retries: 模型空内容且无工具调用时的有限恢复次数。
            max_inline_tool_result_tokens: 工具结果内联到模型上下文的最大估算值；
                超过后保存为 Artifact，只向模型提供摘要、片段和来源引用。
            artifact_store: Artifact 持久化位置；未提供时使用当前会话目录。
            model_adapter: 统一不同 Provider 返回格式与能力差异的适配器。

        初始化只建立依赖和运行策略，不会调用模型或修改会话历史。
        """
        self.client = client
        self.model_adapter = model_adapter or OpenAIChatCompletionsAdapter(client)
        self.model_turn_driver = ModelTurnDriver(
            adapter=self.model_adapter,
            build_request=lambda messages, tools, stream: self._chat_completion_kwargs(
                messages, tools, stream
            ),
            retry_without_tool_choice=self._retry_without_tool_choice,
            provider_rejects_streaming=self._provider_rejects_streaming,
            usage_from_sdk=self._usage_from_sdk_object,
            extract_text_tool_calls=self._extract_text_tool_calls,
            text_tool_call_start=self._text_tool_call_start,
            build_tool_call_record=self._build_tool_call_record,
        )
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
        self.permission_state = PermissionState()
        self.token_counter = TokenCounter(model=model)
        self.cancellation_controller = cancellation_controller
        self.last_cancellation_token: CancellationToken | None = None
        self.hook_registry = hook_registry or HookRegistry()
        self.max_stop_hook_attempts = max(0, max_stop_hook_attempts)
        self.empty_final_retries = max(0, empty_final_retries)
        self.max_inline_tool_result_tokens = max(1, max_inline_tool_result_tokens)
        history_path = getattr(self.store, "history_path", None)
        artifact_root = history_path.parent / "artifacts" if history_path else None
        self.artifact_store = artifact_store or ArtifactStore(root=artifact_root)
        self.last_tool_results: list[ToolResult] = []
        self.last_segment_result: SegmentResult | None = None
        self.last_segment_checkpoint: dict[str, Any] = {}
        self.recovery_policy = RecoveryPolicy(
            max_tool_iterations=self.max_tool_iterations,
            retry_base_delay=self.retry_base_delay,
            retry_max_delay=self.retry_max_delay,
        )
        self.tool_call_coordinator = ToolCallCoordinator(
            registry=self.registry,
            permission_state=self.permission_state,
            confirm_tool=self.confirm_tool,
            auto_approve=self.auto_approve,
            max_tool_retries=self.max_tool_retries,
            retry_sleep=self.retry_sleep,
            recovery_policy=self.recovery_policy,
        )
        self.context_preparation_service = ContextPreparationService(
            store=self.store,
            registry=self.registry,
            system_prompt=self.system_prompt,
            prompt_manager=self.prompt_manager,
            token_counter=self._estimate_text_tokens,
            memory_injection_limit=self.memory_injection_limit,
            max_output_tokens=self.max_tokens,
            supports_prompt_cache=self.model_adapter.capabilities.supports_prompt_cache,
        )

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
        if self.tool_choice == "none":
            return []
        if "<tool_call" not in content.lower():
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
        if self.tool_choice == "none" or not tools:
            return -1
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
        return self.model_adapter.create_completion(retry_kwargs)

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
    ) -> tuple[str, list[dict[str, Any]], bool, TokenUsage, str | None]:
        """通过 ModelTurnDriver 执行非流式请求，并保留旧私有入口兼容。"""
        return self.model_turn_driver.complete_once(messages, tools)

    def _stream_once(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        on_text_chunk: Callable[[str], None] | None,
        on_stream_event: Callable[[ModelStreamEvent], None] | None = None,
    ) -> tuple[str, list[dict[str, Any]], bool, TokenUsage, str | None]:
        """通过 ModelTurnDriver 消费流式响应，并保留旧私有入口兼容。"""
        return self.model_turn_driver.stream_once(
            messages,
            tools,
            on_text_chunk,
            on_stream_event,
        )

    def _requires_tool_confirmation(self, risk_level: str) -> bool:
        """Return whether a tool call needs interactive approval."""
        return self.tool_call_coordinator.requires_confirmation(risk_level)

    def _is_tool_execution_approved(
        self,
        name: str,
        arguments: dict[str, Any],
        risk_level: str | None = None,
    ) -> bool:
        """Check whether a requested tool call is allowed to execute."""
        return self.tool_call_coordinator.is_approved(name, arguments, risk_level)

    def _record_permission_request(
        self,
        *,
        name: str,
        arguments: dict[str, Any],
        risk_level: str,
        tool_call_id: str,
        turn_state: TurnState,
    ) -> str:
        """Create a PermissionState request and mirror legacy turn fields."""
        return self.tool_call_coordinator.record_permission_request(
            name=name,
            arguments=arguments,
            risk_level=risk_level,
            tool_call_id=tool_call_id,
            turn_state=turn_state,
        )

    def _record_permission_approval(
        self,
        request_id: str,
        *,
        turn_state: TurnState,
        reason: str = "user approved tool execution",
    ) -> None:
        """Record a one-shot approval for the active tool request."""
        self.tool_call_coordinator.record_permission_approval(
            request_id, turn_state=turn_state, reason=reason
        )

    def _record_permission_denial(
        self,
        request_id: str,
        *,
        turn_state: TurnState,
        reason: str,
    ) -> None:
        """Record a denied permission request in both new and legacy state."""
        self.tool_call_coordinator.record_permission_denial(
            request_id, turn_state=turn_state, reason=reason
        )

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
        return self.recovery_policy.repetition_stop_message(signature)

    def _iteration_stop_message(self) -> str:
        """Build the final response when the tool loop reaches its safety limit."""
        return self.recovery_policy.iteration_stop_message()

    def _format_retried_error(self, error: ToolError, attempts: int) -> str:
        """Annotate the final retryable error with retry context."""
        return self.recovery_policy.format_retried_error(error, attempts)

    def _retry_delay_for_error(self, error: ToolError, retry_index: int) -> float:
        """Return the bounded delay before the next automatic retry."""
        return self.recovery_policy.retry_delay(error, retry_index)

    def _execute_tool_with_retries(
        self,
        name: str,
        args: dict[str, Any],
        *,
        cancellation_token: CancellationToken | None = None,
    ) -> ToolExecutionOutcome:
        """Execute a tool with bounded retries for structured retryable errors."""
        return self.tool_call_coordinator.execute_with_retries(
            name, args, cancellation_token=cancellation_token
        )

    def _call_registry_tool(
        self,
        name: str,
        args: dict[str, Any],
        cancellation_token: CancellationToken | None,
    ) -> Any:
        """Call registries with native cancellation when they support it."""
        return self.tool_call_coordinator.call_registry(
            name, args, cancellation_token=cancellation_token
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

    def _run_hook_until_decision(self, context: HookContext) -> HookResult:
        """Run hooks and return the first non-continue decision."""
        return self.hook_registry.run_until_decision(context)

    def _hook_block_message(self, result: HookResult) -> str:
        return result.message or result.reason or "Execution blocked by hook."

    def _stop_hook_retry_message(self, result: HookResult) -> str:
        return result.retry_prompt or result.message or (
            "[STOP_HOOK_RETRY]\n"
            "The previous response appears incomplete or premature. Continue the "
            "same task, use available context, and provide the missing result."
        )

    def _empty_final_retry_message(self) -> str:
        return (
            "[FINAL_RESPONSE_REQUIRED]\n"
            "The previous model response contained neither assistant content nor "
            "a tool call, so it cannot complete the task. Continue the same task. "
            "If work is complete, provide a final answer in normal assistant content; "
            "otherwise call the next required tool or clearly explain the blocker."
        )

    def _compact_tool_result_for_model(
        self,
        *,
        content: str,
        tool_name: str,
        tool_call_id: str,
        turn_id: str,
        session_id: str,
    ) -> tuple[str, list[dict[str, Any]]]:
        """将超长工具输出 Artifact 化，并返回有界的模型可见内容。

        未超过 ``max_inline_tool_result_tokens`` 时原样返回且不创建 Artifact。
        超限时保存完整文本，模型侧只获得摘要、首尾片段、大小、校验值和
        Artifact ID；返回的第二项是 Artifact 记录，用于挂到 ToolResult、
        TurnState 和 Segment 检查点。保存失败会向上抛出，避免模型误以为完整
        输出已经可靠持久化。
        """
        estimated_tokens = self._estimate_text_tokens(content)
        if estimated_tokens <= self.max_inline_tool_result_tokens:
            return content, []

        collapsed = " ".join(content.split())
        summary = collapsed[:240]
        if len(collapsed) > 240:
            summary += "..."
        artifact = self.artifact_store.save_text(
            content,
            kind="tool-result",
            summary=f"{tool_name}: {summary}",
            session_id=session_id,
            turn_id=turn_id,
            metadata={
                "tool_name": tool_name,
                "tool_call_id": tool_call_id,
                "estimated_tokens": estimated_tokens,
            },
        )
        excerpt_chars = max(240, min(1200, self.max_inline_tool_result_tokens * 2))
        head = content[:excerpt_chars].strip()
        tail = content[-excerpt_chars:].strip() if len(content) > excerpt_chars else ""
        excerpts = f"Beginning:\n{head}"
        if tail and tail != head:
            excerpts += f"\n\nEnd:\n{tail}"
        model_content = (
            f"[Large tool result stored as Artifact {artifact.artifact_id}]\n"
            f"Summary: {summary or '(no textual summary)'}\n"
            f"Original size: {artifact.size_bytes} bytes; estimated tokens: "
            f"{estimated_tokens}.\n{excerpts}\n"
            f"Source: {artifact.artifact_id} ({artifact.checksum})."
        )
        return model_content, [artifact.to_record()]

    def _build_segment_result(self, reply: str) -> SegmentResult:
        """根据最近一次 TurnState 构造统一的 SegmentResult。

        正常情况下状态和原因取自 TurnTermination，并附带工具计数和检查点。
        如果执行异常到连 TurnState 或终止记录都没有建立，则返回 failed 结果，
        让上层仍能得到明确、可观测的结束原因。
        """
        turn = self.last_turn_state
        if turn is None:
            return SegmentResult(
                status=TurnStatus.FAILED,
                reason="missing_turn_state",
                reply=reply,
            )
        termination = turn.termination
        status = termination.status if termination is not None else TurnStatus.FAILED
        reason = termination.reason if termination is not None else "missing_termination"
        loop = turn.loop
        return SegmentResult(
            status=status,
            reason=reason,
            reply=reply,
            tool_iterations=loop.tool_iterations if loop is not None else 0,
            tool_calls=turn.usage.tool_calls,
            checkpoint=dict(self.last_segment_checkpoint),
            remaining_work_summary=str(
                self.last_segment_checkpoint.get("remaining_work_summary", "")
            ),
            turn=turn,
        )

    def run_segment(
        self,
        user_input: str,
        stream: bool = True,
        on_text_chunk: Callable[[str], None] | None = None,
        on_stream_event: Callable[[ModelStreamEvent], None] | None = None,
        runtime_instructions: list[str] | None = None,
    ) -> SegmentResult:
        """运行一个有界 Segment，并返回结构化执行结果。

        本方法先清空上一段检查点，再调用保持字符串返回兼容性的 ``run()``。
        成功、让出、阻塞或取消都会从 TurnState 转换为 SegmentResult；异常时也会
        先记录最后可获得的分段结果再继续抛出，供 QueryEngine 统一处理失败事件。
        """
        self.last_segment_checkpoint = {}
        try:
            reply = self.run(
                user_input,
                stream=stream,
                on_text_chunk=on_text_chunk,
                on_stream_event=on_stream_event,
                runtime_instructions=runtime_instructions,
            )
        except Exception as exc:
            self.last_segment_result = self._build_segment_result(str(exc))
            raise
        self.last_segment_result = self._build_segment_result(reply)
        return self.last_segment_result

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
        return self.context_preparation_service.build_system_prompt(user_input)

    def _no_progress_stop_message(
        self,
        signal: ProgressSignal,
        *,
        after_reflection: bool = False,
    ) -> str:
        """Build the final response when completed tool rounds make no progress."""
        return self.recovery_policy.no_progress_stop_message(
            signal, after_reflection=after_reflection
        )

    def _reflection_prompt(self, signal: ProgressSignal) -> str:
        """Build a bounded runtime-only prompt that asks the model to change approach."""
        return self.recovery_policy.reflection_prompt(signal)

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
        on_stream_event: Callable[[ModelStreamEvent], None] | None = None,
        runtime_instructions: list[str] | None = None,
    ) -> str:
        """执行一次 Agent Turn，并兼容旧调用方所需的字符串返回值。

        实际状态机在 ``_run_turn`` 中运行。本层是异常保护边界：未捕获异常发生时
        会补写 failed 终止状态、执行 Turn/Session 结束 Hook、提交本轮用量并结束
        取消控制器，保证终端不会在没有原因和持久化记录的情况下静默停止。
        """
        try:
            return self._run_turn(
                user_input,
                stream=stream,
                on_text_chunk=on_text_chunk,
                on_stream_event=on_stream_event,
                runtime_instructions=runtime_instructions,
            )
        except Exception as exc:
            turn_state = self.last_turn_state
            if turn_state is not None and turn_state.termination is None:
                turn_state.fail(str(exc))
                try:
                    session_id = str(getattr(self.store, "session_id", "default"))
                    context = HookContext(
                        hook_type=HookType.TURN_END,
                        session_id=session_id,
                        turn_id=turn_state.turn_id,
                        model=self.model,
                        user_input=user_input,
                        error=str(exc),
                        metadata={"status": "failed", "reason": str(exc)},
                    )
                    self.hook_registry.run(context)
                    self.hook_registry.run(
                        HookContext(
                            hook_type=HookType.SESSION_END,
                            session_id=session_id,
                            turn_id=turn_state.turn_id,
                            model=self.model,
                            user_input=user_input,
                            error=str(exc),
                            metadata={"status": "failed", "reason": str(exc)},
                        )
                    )
                except Exception:
                    pass
            if self.cancellation_controller is not None:
                self.cancellation_controller.finish_turn()
            raise

    def _run_turn(
        self,
        user_input: str,
        stream: bool = True,
        on_text_chunk: Callable[[str], None] | None = None,
        on_stream_event: Callable[[ModelStreamEvent], None] | None = None,
        runtime_instructions: list[str] | None = None,
    ) -> str:
        """执行单个 Segment 的完整模型—工具状态循环。

        流程依次为：组装并核算上下文、创建 Turn/Loop/取消状态、执行启动 Hook、
        循环调用模型与工具、检测重复或无进展、处理空回复恢复，最后进入完成、
        让出、阻塞、失败或取消之一。``runtime_instructions`` 仅对当前模型请求
        可见，不写入持久历史；工具 Observation 和安全检查点会提交到历史中。

        ``max_tool_iterations`` 与上下文上限都是 Segment 边界：已有工具进展时
        生成 yielded 检查点交给 QueryEngine 续跑；如果固定上下文本身已无法装入
        模型窗口，则明确 blocked。返回值保持旧接口兼容，真实状态应读取
        ``last_turn_state`` 或通过 ``run_segment`` 获取。
        """
        # 1. 在调用模型前统一组装上下文、预算与运行时指令。
        prepared_context = self.context_preparation_service.prepare(
            user_input, runtime_instructions
        )
        tools = prepared_context.tools
        max_context_tokens = prepared_context.max_context_tokens
        tool_schema_tokens = prepared_context.tool_schema_tokens
        output_reserve_tokens = prepared_context.output_reserve_tokens
        self.last_context_window = prepared_context.compression
        message_store = prepared_context.message_store
        user_message = {"role": "user", "content": user_input}
        self.last_message_store = message_store
        turn_usage = TokenUsage()
        turn_state = TurnState(user_input=user_input)
        turn_state.start()
        loop_state = turn_state.start_loop()
        self.last_turn_state = turn_state
        self.last_loop_state = loop_state
        self.last_tool_results = []
        session_id = getattr(self.store, "session_id", None)
        if self.cancellation_controller is None:
            self.cancellation_controller = CancellationController(
                session_id=str(session_id or "default")
            )
        turn_token = self.cancellation_controller.start_turn(turn_state.turn_id)
        task_token = self.cancellation_controller.start_task(
            f"{turn_state.turn_id}:agent-loop",
            turn_token=turn_token,
        )
        turn_state.cancellation_token = turn_token
        self.last_cancellation_token = turn_token

        def run_end_hooks(
            *,
            status: str,
            final_response: str = "",
            error: str | None = None,
            reason: str | None = None,
        ) -> None:
            """以同一终止元数据执行 Turn 与 Session 结束 Hook。

            ``reason`` 用于 yielded 等非错误终态，``error`` 只表示真实错误或阻塞；
            两者分离可避免监控端把安全换段误报为失败。
            """
            termination_reason = reason or error or (
                "model_completed" if status == "completed" else status
            )
            termination_metadata = {
                "status": status,
                "reason": termination_reason,
                "provider_finish_reason": turn_state.provider_finish_reason,
            }
            context = HookContext(
                hook_type=HookType.TURN_END,
                session_id=str(session_id or "default"),
                turn_id=turn_state.turn_id,
                model=self.model,
                user_input=user_input,
                final_response=final_response,
                error=error,
                metadata=termination_metadata,
            )
            self.hook_registry.run(context)
            self.hook_registry.run(
                HookContext(
                    hook_type=HookType.SESSION_END,
                    session_id=str(session_id or "default"),
                    turn_id=turn_state.turn_id,
                    model=self.model,
                    user_input=user_input,
                    final_response=final_response,
                    error=error,
                    metadata=termination_metadata,
                )
            )

        def block_current_turn(
            message: str,
            reason: str = "hook_blocked",
            *,
            recovery_attempts: int = 0,
        ) -> str:
            """把当前 Turn 明确标记为阻塞，并提交可恢复的最终消息。

            阻塞前回滚尚未形成完整协议链的 pending 消息，再只提交用户请求和
            阻塞说明，防止残缺 tool_call 污染下轮上下文。随后保存用量、执行结束
            Hook 并释放取消控制器。
            """
            turn_state.block(
                message,
                reason=reason,
                recovery_attempts=recovery_attempts,
            )
            message_store.rollback_pending()
            message_store.pending_messages.append(dict(user_message))
            message_store.append_pending({"role": "assistant", "content": message})
            message_store.commit(self.store.append)
            self._commit_turn_usage(turn_usage)
            run_end_hooks(status="blocked", final_response=message, error=reason)
            self.cancellation_controller.finish_turn()
            return message

        def cancel_current_turn(reason: str, partial_response: str = "") -> str:
            """传播取消原因、丢弃未提交消息并结束当前 Turn。

            取消不提交半成品工具协议，但允许把已经流出的文本作为返回值交给界面；
            终止原因和用量仍会被持久化，保证用户能区分取消与异常失败。
            """
            if not turn_token.is_cancelled:
                turn_token.cancel(reason)
            turn_state.cancel(reason)
            message_store.rollback_pending()
            self._commit_turn_usage(turn_usage)
            run_end_hooks(status="cancelled", final_response=partial_response, error=reason)
            self.cancellation_controller.finish_turn()
            return partial_response

        def yield_current_turn(reason: str) -> str:
            """在安全边界提交检查点，并将控制权交给 QueryEngine 自动续段。

            已完成的工具调用和 Observation 会先完整提交，然后写入一个明确的
            SEGMENT_CHECKPOINT。检查点保存原任务、计数、Artifact、上下文预算和
            剩余工作说明；该路径状态为 yielded，不是 completed 或 failed。
            """
            remaining_work_summary = (
                "Continue the same user task from the committed tool observations. "
                "Re-check what remains, then use tools or provide the final answer."
            )
            checkpoint_reply = (
                "[SEGMENT_CHECKPOINT] Execution reached a safe segment boundary. "
                f"Reason: {reason}. {remaining_work_summary}"
            )
            message_store.append_pending(
                {"role": "assistant", "content": checkpoint_reply}
            )
            message_store.commit(self.store.append)
            turn_state.final_response = checkpoint_reply
            turn_state.yield_control(reason)
            self._commit_turn_usage(turn_usage)
            self.last_segment_checkpoint = {
                "turn_id": turn_state.turn_id,
                "reason": reason,
                "original_user_input": user_input,
                "model_iterations": loop_state.model_iterations,
                "tool_iterations": loop_state.tool_iterations,
                "tool_calls": turn_usage.tool_calls,
                "artifacts": list(turn_state.artifacts),
                "context_window": dict(self.last_context_window),
                "remaining_work_summary": remaining_work_summary,
            }
            turn_state.checkpoint = dict(self.last_segment_checkpoint)
            run_end_hooks(
                status="yielded",
                final_response=checkpoint_reply,
                reason=reason,
            )
            self.cancellation_controller.finish_turn()
            return checkpoint_reply

        if task_token.is_cancelled:
            return cancel_current_turn(task_token.reason or "cancelled")

        load_usage_state = getattr(self.store, "load_usage_state", None)
        if callable(load_usage_state):
            self.usage_state = load_usage_state()
        self.usage_state.start_turn(turn_state.turn_id)
        self.usage_state.set_conversation(session_id)
        turn_state.usage_state = self.usage_state

        for hook_type in (HookType.SESSION_START, HookType.TURN_START):
            hook_result = self._run_hook_until_decision(
                HookContext(
                    hook_type=hook_type,
                    session_id=str(session_id or "default"),
                    turn_id=turn_state.turn_id,
                    model=self.model,
                    user_input=user_input,
                )
            )
            if hook_result.normalized_decision() is HookDecision.BLOCK:
                return block_current_turn(
                    self._hook_block_message(hook_result),
                    reason=hook_result.reason or "hook_blocked",
                )
        
        budget_breakdown = dict(
            self.last_context_window.get("budget_breakdown", {}) or {}
        )
        message_tokens = max(
            0,
            int(self.last_context_window.get("total_tokens", 0) or 0)
            - tool_schema_tokens
            - output_reserve_tokens,
        )
        self.last_context_window = {
            **self.last_context_window,
            "message_tokens": message_tokens,
            "tool_schema_tokens": tool_schema_tokens,
            "output_reserve_tokens": output_reserve_tokens,
            "budget_breakdown": budget_breakdown,
        }

        # 2. 进入模型—工具循环；每轮开始都重新核算动态增长的消息上下文。
        tool_iterations = 0
        consecutive_signature: tuple[str, str] | None = None
        consecutive_count = 0
        empty_final_recovery_attempts = 0
        progress_monitor = ProgressMonitor()
        self.last_progress_signal = None
        self.last_reflection_count = 0
        while True:
            if task_token.is_cancelled:
                return cancel_current_turn(task_token.reason or "cancelled")
            if tool_iterations >= self.max_tool_iterations:
                return yield_current_turn("segment_tool_iteration_limit")

            model_context_messages = message_store.prepare_model_context()
            estimate_messages = getattr(
                self.store, "estimate_messages_tokens", self._estimate_messages_tokens
            )
            current_message_tokens = estimate_messages(model_context_messages)
            current_total_tokens = (
                current_message_tokens
                + tool_schema_tokens
                + output_reserve_tokens
            )
            self.last_context_window = {
                **self.last_context_window,
                "message_tokens": current_message_tokens,
                "tool_schema_tokens": tool_schema_tokens,
                "output_reserve_tokens": output_reserve_tokens,
                "total_tokens": current_total_tokens,
                "remaining_tokens": max(
                    0, max_context_tokens - current_total_tokens
                ),
                "over_budget": current_total_tokens > max_context_tokens,
            }
            if current_total_tokens > max_context_tokens:
                if tool_iterations > 0:
                    return yield_current_turn("segment_context_budget")
                return block_current_turn(
                    "Task blocked because the assembled system instructions, pinned "
                    "facts, current request, tool schema, runtime controls, and output "
                    "reserve exceed the model context window. Reduce enabled tools, "
                    "instructions, task text, or model.max_tokens.",
                    reason="context_budget_exhausted",
                )
            if task_token.is_cancelled:
                return cancel_current_turn(task_token.reason or "cancelled")
            before_model = self._run_hook_until_decision(
                HookContext(
                    hook_type=HookType.BEFORE_MODEL,
                    session_id=str(session_id or "default"),
                    turn_id=turn_state.turn_id,
                    model=self.model,
                    user_input=user_input,
                    messages=model_context_messages,
                    tools=tools,
                    metadata={"tool_iteration": tool_iterations, "stream": stream},
                )
            )
            before_model_decision = before_model.normalized_decision()
            if before_model_decision is HookDecision.BLOCK:
                return block_current_turn(
                    self._hook_block_message(before_model),
                    reason=before_model.reason or "hook_blocked",
                )
            if (
                before_model_decision is HookDecision.MODIFY
                and before_model.modified_messages is not None
            ):
                model_context_messages = before_model.modified_messages
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
                (
                    assistant_content,
                    tool_calls_raw,
                    interrupted,
                    call_usage,
                    provider_finish_reason,
                ) = self._stream_once(
                    messages=model_context_messages,
                    tools=tools,
                    on_text_chunk=on_text_chunk,
                    on_stream_event=on_stream_event,
                )
            else:
                loop_state.record_model_call()
                (
                    assistant_content,
                    tool_calls_raw,
                    interrupted,
                    call_usage,
                    provider_finish_reason,
                ) = self._complete_once(
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

            after_model = self._run_hook_until_decision(
                HookContext(
                    hook_type=HookType.AFTER_MODEL,
                    session_id=str(session_id or "default"),
                    turn_id=turn_state.turn_id,
                    model=self.model,
                    user_input=user_input,
                    messages=model_context_messages,
                    tools=tools,
                    final_response=assistant_content,
                    metadata={
                        "tool_iteration": tool_iterations,
                        "interrupted": interrupted,
                        "tool_call_count": len(tool_calls_raw),
                        "provider_finish_reason": provider_finish_reason,
                    },
                )
            )
            turn_state.record_provider_finish_reason(provider_finish_reason)
            after_model_decision = after_model.normalized_decision()
            if after_model_decision is HookDecision.BLOCK:
                return block_current_turn(
                    self._hook_block_message(after_model),
                    reason=after_model.reason or "hook_blocked",
                )
            if (
                after_model_decision is HookDecision.MODIFY
                and after_model.modified_response is not None
            ):
                assistant_content = after_model.modified_response

            if interrupted:
                # Interrupted turns intentionally do not write partial assistant
                # or tool state, preserving a resumable conversation history.
                turn_state.cancel("interrupted")
                message_store.rollback_pending()
                self._commit_turn_usage(turn_usage)
                turn_token.cancel("interrupted")
                run_end_hooks(
                    status="cancelled",
                    final_response=assistant_content,
                    error="interrupted",
                )
                self.cancellation_controller.finish_turn()
                return assistant_content

            # If it's a simple text reply, we are done
            if not tool_calls_raw:
                if not assistant_content.strip():
                    if empty_final_recovery_attempts >= self.empty_final_retries:
                        context_tokens = int(
                            self.last_context_window.get("total_tokens", 0) or 0
                        )
                        finish_detail = (
                            f"（Provider finish reason: {provider_finish_reason}）"
                            if provider_finish_reason
                            else "（Provider 未提供 finish reason）"
                        )
                        return block_current_turn(
                            "任务已阻塞：模型连续返回空内容且没有工具调用，"
                            f"已自动恢复 {empty_final_recovery_attempts} 次仍未成功。"
                            f"模型调用 {loop_state.model_iterations} 次，"
                            f"工具轮次 {loop_state.tool_iterations} 次，"
                            f"当前上下文估算 {context_tokens} tokens。"
                            f"{finish_detail} 可在终端输入“继续”重新提交后续请求。",
                            reason="empty_model_response",
                            recovery_attempts=empty_final_recovery_attempts,
                        )
                    empty_final_recovery_attempts += 1
                    if on_stream_event is not None:
                        on_stream_event(
                            ModelStreamEvent.status(
                                "model.empty_response_retry",
                                attempt=empty_final_recovery_attempts,
                                max_attempts=self.empty_final_retries,
                                provider_finish_reason=provider_finish_reason,
                            )
                        )
                    message_store.append_runtime_only(
                        {
                            "role": "system",
                            "content": self._empty_final_retry_message(),
                        }
                    )
                    continue
                final_reply = assistant_content
                stop_result = self._run_hook_until_decision(
                    HookContext(
                        hook_type=HookType.STOP,
                        session_id=str(session_id or "default"),
                        turn_id=turn_state.turn_id,
                        model=self.model,
                        user_input=user_input,
                        messages=message_store.prepare_model_context(),
                        tools=tools,
                        final_response=final_reply,
                        metadata={
                            "tool_iteration": tool_iterations,
                            "stop_hook_attempts": loop_state.stop_hook_attempts,
                        },
                    )
                )
                stop_decision = stop_result.normalized_decision()
                if stop_decision is HookDecision.RETRY:
                    if loop_state.stop_hook_attempts >= self.max_stop_hook_attempts:
                        block_message = (
                            "Blocked because Stop Hook retry limit was reached."
                        )
                        return block_current_turn(
                            block_message,
                            reason=stop_result.reason or "stop_hook_retry_limit",
                        )
                    loop_state.activate_stop_hook()
                    message_store.append_runtime_only(
                        {
                            "role": "system",
                            "content": self._stop_hook_retry_message(stop_result),
                        }
                    )
                    continue
                if stop_decision is HookDecision.BLOCK:
                    return block_current_turn(
                        self._hook_block_message(stop_result),
                        reason=stop_result.reason or "hook_blocked",
                    )
                if (
                    stop_decision is HookDecision.MODIFY
                    and stop_result.modified_response is not None
                ):
                    final_reply = stop_result.modified_response
                loop_state.clear_stop_hook()
                assistant_msg = {"role": "assistant", "content": final_reply}
                message_store.append_pending(assistant_msg)

                # Only the final assistant reply marks the turn as complete.
                message_store.commit(self.store.append)
                turn_state.complete(
                    final_reply,
                    usage=turn_usage,
                    reason="model_completed",
                )
                self._commit_turn_usage(turn_usage)
                run_end_hooks(status="completed", final_response=final_reply)
                self.cancellation_controller.finish_turn()
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
                return block_current_turn(
                    final_reply,
                    reason=LoopTransition.DUPLICATE_CALL_LIMIT.value,
                )

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
                if task_token.is_cancelled:
                    return cancel_current_turn(task_token.reason or "cancelled")
                name = ""
                args: dict[str, Any] = {}
                risk_level = "unknown"
                observation_success = False
                observation_retryable = False
                tool_status = "error"
                started_at = perf_counter()
                try:
                    # Parse arguments and call the tool through the registry
                    name = tc["function"]["name"]
                    args = json.loads(tc["function"]["arguments"])
                    risk_level = self.registry.get_tool_meta(name).get("risk_level", "low")
                    before_tool = self._run_hook_until_decision(
                        HookContext(
                            hook_type=HookType.BEFORE_TOOL,
                            session_id=str(session_id or "default"),
                            turn_id=turn_state.turn_id,
                            model=self.model,
                            user_input=user_input,
                            tool_name=name,
                            tool_call_id=tc["id"],
                            tool_arguments=args,
                            risk_level=risk_level,
                        )
                    )
                    before_tool_decision = before_tool.normalized_decision()
                    if before_tool_decision is HookDecision.BLOCK:
                        return block_current_turn(
                            self._hook_block_message(before_tool),
                            reason=before_tool.reason or "hook_blocked",
                        )
                    if (
                        before_tool_decision is HookDecision.MODIFY
                        and before_tool.modified_arguments is not None
                    ):
                        args = before_tool.modified_arguments
                    # Validate after hooks have had their only chance to modify
                    # arguments, but before permission prompts or tool side effects.
                    args = self.registry.validate_arguments(name, args)
                    request_id: str | None = None
                    granted_decision = self.permission_state.find_active_grant(
                        tool_name=name,
                        arguments=args,
                    )
                    if granted_decision is None and self._requires_tool_confirmation(risk_level):
                        loop_state.await_permission()
                        request_id = self._record_permission_request(
                            name=name,
                            arguments=args,
                            risk_level=risk_level,
                            tool_call_id=tc["id"],
                            turn_state=turn_state,
                        )
                    if granted_decision is None and not self._is_tool_execution_approved(
                        name,
                        args,
                        risk_level,
                    ):
                        loop_state.record_permission_denial()
                        tool_status = "denied"
                        if request_id is not None:
                            self._record_permission_denial(
                                request_id,
                                turn_state=turn_state,
                                reason="user denied tool execution",
                            )
                        result_str = "User denied tool execution."
                        duration_ms = (perf_counter() - started_at) * 1000
                        after_tool = self._run_hook_until_decision(
                            HookContext(
                                hook_type=HookType.AFTER_TOOL,
                                session_id=str(session_id or "default"),
                                turn_id=turn_state.turn_id,
                                model=self.model,
                                user_input=user_input,
                                tool_name=name,
                                tool_call_id=tc["id"],
                                tool_arguments=args,
                                tool_result=result_str,
                                risk_level=risk_level,
                                metadata={"status": "denied"},
                            )
                        )
                        if after_tool.normalized_decision() is HookDecision.BLOCK:
                            return block_current_turn(
                                self._hook_block_message(after_tool),
                                reason=after_tool.reason or "hook_blocked",
                            )
                        if (
                            after_tool.normalized_decision() is HookDecision.MODIFY
                            and after_tool.modified_response is not None
                        ):
                            result_str = after_tool.modified_response
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
                        if request_id is not None:
                            self._record_permission_approval(
                                request_id,
                                turn_state=turn_state,
                            )
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
                        tool_token = self.cancellation_controller.create_child(
                            tc["id"],
                            parent=task_token,
                            scope="tool",
                        )
                        outcome = self._execute_tool_with_retries(
                            name,
                            args,
                            cancellation_token=tool_token,
                        )
                        result_str = outcome.content
                        duration_ms = (perf_counter() - started_at) * 1000
                        after_tool = self._run_hook_until_decision(
                            HookContext(
                                hook_type=HookType.AFTER_TOOL,
                                session_id=str(session_id or "default"),
                                turn_id=turn_state.turn_id,
                                model=self.model,
                                user_input=user_input,
                                tool_name=name,
                                tool_call_id=tc["id"],
                                tool_arguments=args,
                                tool_result=result_str,
                                risk_level=risk_level,
                                metadata={"status": "success" if outcome.success else "error"},
                            )
                        )
                        if after_tool.normalized_decision() is HookDecision.BLOCK:
                            return block_current_turn(
                                self._hook_block_message(after_tool),
                                reason=after_tool.reason or "hook_blocked",
                            )
                        if (
                            after_tool.normalized_decision() is HookDecision.MODIFY
                            and after_tool.modified_response is not None
                        ):
                            result_str = after_tool.modified_response
                        if outcome.success:
                            tool_status = "success"
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
                            tool_status = "error"
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
                except CancellationError as exc:
                    return cancel_current_turn(exc.token.reason or "cancelled")
                except Exception as e:
                    # Capture tool execution errors and feed them back to the model
                    error = tool_error_from_exception(e)
                    tool_status = "error"
                    observation_retryable = error.retryable
                    result_str = error.to_json()
                    duration_ms = (perf_counter() - started_at) * 1000
                    tool_error_hook = self._run_hook_until_decision(
                        HookContext(
                            hook_type=HookType.TOOL_ERROR,
                            session_id=str(session_id or "default"),
                            turn_id=turn_state.turn_id,
                            model=self.model,
                            user_input=user_input,
                            tool_name=name or "<unknown>",
                            tool_call_id=tc.get("id", ""),
                            tool_arguments=args,
                            tool_result=result_str,
                            risk_level=risk_level,
                            error=error.message,
                        )
                    )
                    if tool_error_hook.normalized_decision() is HookDecision.BLOCK:
                        return block_current_turn(
                            self._hook_block_message(tool_error_hook),
                            reason=tool_error_hook.reason or "hook_blocked",
                        )
                    if (
                        tool_error_hook.normalized_decision() is HookDecision.MODIFY
                        and tool_error_hook.modified_response is not None
                    ):
                        result_str = tool_error_hook.modified_response
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

                model_result_content, result_artifacts = (
                    self._compact_tool_result_for_model(
                        content=result_str,
                        tool_name=name or "<unknown>",
                        tool_call_id=tc["id"],
                        turn_id=turn_state.turn_id,
                        session_id=str(session_id or "default"),
                    )
                )
                structured_result = ToolResult.from_text(
                    tool_call_id=tc["id"],
                    tool_name=name or "<unknown>",
                    status=tool_status,
                    content=result_str,
                    artifacts=result_artifacts,
                    metadata={
                        "risk_level": risk_level,
                        "retryable": observation_retryable,
                        "full_result_artifactized": bool(result_artifacts),
                    },
                )
                structured_result.model_content = model_result_content
                self.last_tool_results.append(structured_result)
                turn_state.tool_results.append(structured_result.to_record())
                turn_state.artifacts.extend(structured_result.artifacts)
                # UI 只消费结构化结果事实；在模型回填前发布，确保终端与持久状态
                # 看到同一个 ToolResult，同时不让 Renderer 反向解析自然语言输出。
                if on_stream_event is not None:
                    on_stream_event(
                        ModelStreamEvent.tool_result(
                            str(structured_result.display_content.get("text") or ""),
                            tool_call_id=structured_result.tool_call_id,
                            tool_name=structured_result.tool_name,
                            tool_result=structured_result.to_record(),
                            arguments=dict(args),
                            risk_level=risk_level,
                        )
                    )
                tool_result_msg = structured_result.to_model_message()
                message_store.append_pending(tool_result_msg)
                observation_content = result_str
                if result_artifacts:
                    artifact_record = result_artifacts[0]
                    observation_content = (
                        "[artifactized tool result] "
                        f"checksum={artifact_record.get('checksum', '')} "
                        f"size_bytes={artifact_record.get('size_bytes', 0)} "
                        f"summary={artifact_record.get('summary', '')}"
                    )
                round_observations.append(
                    ToolObservation(
                        tool_name=name or "<unknown>",
                        arguments=tc.get("function", {}).get("arguments", ""),
                        content=observation_content,
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
                return block_current_turn(final_reply, reason=progress_signal.reason)
            
            # Continue the loop to let the model process tool results
