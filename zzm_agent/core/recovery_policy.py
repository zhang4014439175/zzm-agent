from __future__ import annotations

from zzm_agent.core.errors import ToolError
from zzm_agent.core.progress_monitor import ProgressSignal


class RecoveryPolicy:
    """集中生成重试、反思和安全停止决策所需的确定性策略结果。"""

    def __init__(
        self,
        *,
        max_tool_iterations: int,
        retry_base_delay: float,
        retry_max_delay: float,
    ) -> None:
        """保存安全上限和退避配置；构造过程不修改 Loop 或消息状态。"""
        self.max_tool_iterations = max_tool_iterations
        self.retry_base_delay = retry_base_delay
        self.retry_max_delay = retry_max_delay

    def repetition_stop_message(self, signature: tuple[str, str]) -> str:
        """为重复工具调用生成明确停止说明，不改变重复计数器。"""
        name, arguments = signature
        return (
            "Stopped tool execution because the model repeatedly requested the "
            f"same tool call: {name}({arguments}). Please adjust the approach "
            "or provide more specific input."
        )

    def iteration_stop_message(self) -> str:
        """为单段工具轮次上限生成兼容提示，边界数来自构造配置。"""
        return (
            "Stopped tool execution because the maximum tool iteration limit "
            f"({self.max_tool_iterations}) was reached. Please narrow the task "
            "or continue with a more specific instruction."
        )

    def no_progress_stop_message(
        self,
        signal: ProgressSignal,
        *,
        after_reflection: bool = False,
    ) -> str:
        """根据无进展信号生成停止原因；是否已反思只影响用户可见上下文。"""
        reflection_context = " after reflection" if after_reflection else ""
        return (
            f"Stopped tool execution because no progress was detected{reflection_context} "
            f"({signal.reason}) after {signal.round_count} tool round(s). "
            f"{signal.detail}"
        )

    def reflection_prompt(self, signal: ProgressSignal) -> str:
        """生成一次性运行时反思提示，调用方决定是否注入以及如何记录状态。"""
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

    def format_retried_error(self, error: ToolError, attempts: int) -> str:
        """把实际重试次数写入结构化错误，并返回供模型恢复使用的 JSON。"""
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

    def retry_delay(self, error: ToolError, retry_index: int) -> float:
        """计算有上限的重试等待时间；服务端建议存在时优先采用。"""
        if error.retry_after_seconds is not None:
            return min(self.retry_max_delay, max(0.0, error.retry_after_seconds))
        return min(self.retry_max_delay, self.retry_base_delay * (2 ** retry_index))
