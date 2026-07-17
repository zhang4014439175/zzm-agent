from __future__ import annotations

from dataclasses import dataclass
from inspect import Parameter, signature
from typing import Any, Callable

from zzm_agent.core.errors import ToolError, tool_error_from_exception
from zzm_agent.core.recovery_policy import RecoveryPolicy
from zzm_agent.core.runtime_state import (
    CancellationToken,
    PermissionScope,
    PermissionState,
    TurnState,
)


@dataclass
class ToolExecutionOutcome:
    """一次受协调工具执行的正文、成功状态、尝试次数和最终错误。"""

    content: str
    success: bool
    attempts: int = 1
    error: ToolError | None = None


class ToolCallCoordinator:
    """统一工具权限、Registry 调用、取消检查和有限自动重试。"""

    def __init__(
        self,
        *,
        registry: Any,
        permission_state: PermissionState,
        confirm_tool: Callable[[str, dict[str, Any], str], bool] | None,
        auto_approve: bool,
        max_tool_retries: int,
        retry_sleep: Callable[[float], None],
        recovery_policy: RecoveryPolicy,
    ) -> None:
        """保存工具运行依赖；构造时不请求权限、不执行工具也不修改 Turn。"""
        self.registry = registry
        self.permission_state = permission_state
        self.confirm_tool = confirm_tool
        self.auto_approve = auto_approve
        self.max_tool_retries = max_tool_retries
        self.retry_sleep = retry_sleep
        self.recovery_policy = recovery_policy

    def requires_confirmation(self, risk_level: str) -> bool:
        """根据自动批准开关和风险等级判断是否需要交互确认。"""
        return not self.auto_approve and risk_level in {"medium", "high"}

    def is_approved(
        self,
        name: str,
        arguments: dict[str, Any],
        risk_level: str | None = None,
    ) -> bool:
        """请求必要的用户确认；缺少确认入口时对高风险调用返回拒绝。"""
        risk = risk_level or self.registry.get_tool_meta(name).get("risk_level", "low")
        if not self.requires_confirmation(risk):
            return True
        if self.confirm_tool is None:
            return False
        return bool(self.confirm_tool(name, arguments, risk))

    def record_permission_request(
        self,
        *,
        name: str,
        arguments: dict[str, Any],
        risk_level: str,
        tool_call_id: str,
        turn_state: TurnState,
    ) -> str:
        """创建一次性权限请求，并同步新旧 Turn 权限字段以保持恢复兼容。"""
        request = self.permission_state.request_permission(
            tool_name=name,
            arguments=arguments,
            risk_level=risk_level,
            tool_call_id=tool_call_id,
            scope=PermissionScope.ONCE,
            turn_id=turn_state.turn_id,
        )
        turn_state.permissions.pending_requests[request.request_id] = request
        turn_state.permission_requests.append(request.to_record())
        return request.request_id

    def record_permission_approval(
        self,
        request_id: str,
        *,
        turn_state: TurnState,
        reason: str = "user approved tool execution",
    ) -> None:
        """记录一次性批准并从当前 Turn 的待处理集合移除请求。"""
        decision = self.permission_state.approve_request(
            request_id,
            scope=PermissionScope.ONCE,
            reason=reason,
        )
        turn_state.permissions.decisions.append(decision)
        turn_state.permissions.pending_requests.pop(request_id, None)

    def record_permission_denial(
        self,
        request_id: str,
        *,
        turn_state: TurnState,
        reason: str,
    ) -> None:
        """记录拒绝并同步兼容字段，确保模型 Observation 和恢复状态一致。"""
        decision = self.permission_state.deny_request(request_id, reason=reason)
        turn_state.permissions.decisions.append(decision)
        turn_state.permissions.denials.append(decision)
        turn_state.permissions.pending_requests.pop(request_id, None)
        turn_state.permission_denials.append(decision.to_record())

    def call_registry(
        self,
        name: str,
        arguments: dict[str, Any],
        cancellation_token: CancellationToken | None,
    ) -> Any:
        """调用新旧 Registry，并在不支持原生取消参数时保留前后检查点。"""
        call = self.registry.call
        try:
            parameters = signature(call).parameters.values()
            supports_cancellation = any(
                item.name == "cancellation_token"
                or item.kind is Parameter.VAR_KEYWORD
                for item in parameters
            )
        except (TypeError, ValueError):
            supports_cancellation = False
        if supports_cancellation:
            return call(name, arguments, cancellation_token=cancellation_token)
        if cancellation_token is not None:
            cancellation_token.raise_if_cancelled()
        result = call(name, arguments)
        if cancellation_token is not None:
            cancellation_token.raise_if_cancelled()
        return result

    def execute_with_retries(
        self,
        name: str,
        arguments: dict[str, Any],
        *,
        cancellation_token: CancellationToken | None = None,
    ) -> ToolExecutionOutcome:
        """执行工具并仅对结构化可重试错误做有界重试。

        每次调用和等待前后都检查取消。成功立即返回；不可重试或次数耗尽时返回
        结构化失败结果而不抛出。取消异常保持抛出，由 Segment 状态机标记取消。
        """
        attempts = 0
        last_error: ToolError | None = None
        max_attempts = self.max_tool_retries + 1
        while attempts < max_attempts:
            if cancellation_token is not None:
                cancellation_token.raise_if_cancelled()
            attempts += 1
            try:
                return ToolExecutionOutcome(
                    content=str(self.call_registry(name, arguments, cancellation_token)),
                    success=True,
                    attempts=attempts,
                )
            except Exception as exc:
                from zzm_agent.core.runtime_state import CancellationError

                if isinstance(exc, CancellationError):
                    raise
                last_error = tool_error_from_exception(exc)
                if not last_error.retryable or attempts >= max_attempts:
                    return ToolExecutionOutcome(
                        content=self.recovery_policy.format_retried_error(last_error, attempts),
                        success=False,
                        attempts=attempts,
                        error=last_error,
                    )
                delay = self.recovery_policy.retry_delay(last_error, attempts - 1)
                if delay > 0:
                    if cancellation_token is not None:
                        cancellation_token.raise_if_cancelled()
                    self.retry_sleep(delay)
                    if cancellation_token is not None:
                        cancellation_token.raise_if_cancelled()
        last_error = last_error or ToolError(
            error_type="ToolExecutionError",
            message="Tool execution failed without an exception payload.",
            recovery_hint="Inspect tool execution logs before retrying.",
            retryable=False,
        )
        return ToolExecutionOutcome(
            content=self.recovery_policy.format_retried_error(last_error, attempts),
            success=False,
            attempts=attempts,
            error=last_error,
        )
