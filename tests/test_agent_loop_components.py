from __future__ import annotations

import json
from unittest.mock import MagicMock

from zzm_agent.core.context_preparation import ContextPreparationService
from zzm_agent.core.errors import ToolError
from zzm_agent.core.recovery_policy import RecoveryPolicy
from zzm_agent.core.runtime_state import PermissionState
from zzm_agent.core.tool_coordinator import ToolCallCoordinator


class _ContextStore:
    """记录上下文准备参数的最小存储替身。"""

    max_context_tokens = 8000

    def __init__(self) -> None:
        self.kwargs = {}

    def load_history(self):
        """返回稳定历史以验证消息分层。"""
        return [{"role": "assistant", "content": "old"}]

    def build_turn_messages(
        self,
        *,
        system_prompt,
        user_input,
        memory_limit,
        tool_schema_tokens=0,
        output_reserve_tokens=0,
        runtime_instruction_tokens=0,
        prompt_cache_strategy="",
    ):
        """捕获预算并返回可构建 MessageStore 的模型上下文。"""
        self.kwargs = locals() | {}
        return (
            [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_input},
            ],
            {"strategy": "none"},
        )


def _policy() -> RecoveryPolicy:
    """构造无真实等待的恢复策略。"""
    return RecoveryPolicy(
        max_tool_iterations=7,
        retry_base_delay=0.5,
        retry_max_delay=2.0,
    )


def test_context_preparation_centralizes_budgets_and_runtime_only_messages():
    """验证工具、输出和临时指令预算统一核算，临时指令不会进入持久消息。"""
    store = _ContextStore()
    registry = MagicMock()
    registry.get_schemas.return_value = [{"type": "function", "name": "demo"}]
    service = ContextPreparationService(
        store=store,
        registry=registry,
        system_prompt="system",
        prompt_manager=None,
        token_counter=len,
        memory_injection_limit=3,
        max_output_tokens=512,
        supports_prompt_cache=True,
    )

    prepared = service.prepare("question", ["  transient  ", ""])

    assert prepared.compression == {"strategy": "none"}
    assert store.kwargs["output_reserve_tokens"] == 512
    assert store.kwargs["runtime_instruction_tokens"] == len("transient")
    assert store.kwargs["prompt_cache_strategy"] == "provider_native"
    assert prepared.message_store.runtime_messages[-1] == {
        "role": "system",
        "content": "transient",
    }
    assert all(
        message.get("content") != "transient"
        for message in prepared.message_store.persisted_messages
    )


def test_tool_call_coordinator_retries_retryable_failures_then_succeeds():
    """验证工具协调器集中执行有限重试并采用恢复策略计算退避。"""
    registry = MagicMock()
    registry.call.side_effect = [TimeoutError("later"), "ok"]
    waits = []
    coordinator = ToolCallCoordinator(
        registry=registry,
        permission_state=PermissionState(),
        confirm_tool=None,
        auto_approve=False,
        max_tool_retries=1,
        retry_sleep=waits.append,
        recovery_policy=_policy(),
    )

    outcome = coordinator.execute_with_retries("demo", {"value": 1})

    assert outcome.success is True
    assert outcome.content == "ok"
    assert outcome.attempts == 2
    assert waits == [0.5]


def test_tool_call_coordinator_keeps_permission_policy_at_component_boundary():
    """验证中高风险确认逻辑从 AgentLoop 移入工具协调器且自动批准仍兼容。"""
    registry = MagicMock()
    registry.get_tool_meta.return_value = {"risk_level": "high"}
    confirm = MagicMock(return_value=True)
    coordinator = ToolCallCoordinator(
        registry=registry,
        permission_state=PermissionState(),
        confirm_tool=confirm,
        auto_approve=False,
        max_tool_retries=0,
        retry_sleep=lambda _: None,
        recovery_policy=_policy(),
    )

    assert coordinator.is_approved("danger", {"force": True}) is True
    confirm.assert_called_once_with("danger", {"force": True}, "high")


def test_recovery_policy_owns_stop_messages_and_bounded_retry_delay():
    """验证停止文案和退避上限由纯策略组件确定。"""
    policy = _policy()
    error = ToolError(
        error_type="TemporaryError",
        message="retry",
        recovery_hint="try again",
        retryable=True,
    )

    payload = json.loads(policy.format_retried_error(error, 2))

    assert "maximum tool iteration limit (7)" in policy.iteration_stop_message()
    assert policy.retry_delay(error, 5) == 2.0
    assert payload["attempts"] == 2
    assert "Automatic retry exhausted" in payload["recovery_hint"]
