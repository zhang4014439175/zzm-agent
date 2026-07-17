from __future__ import annotations

from unittest.mock import MagicMock

from zzm_agent.core.agent_loop import AgentLoop
from zzm_agent.core.model_turn import ModelTurnDriver
from zzm_agent.core.observability import TokenUsage
from zzm_agent.core.tool_registry import ToolRegistry
from zzm_agent.memory.store import MemoryStore


def _loop(tmp_path) -> AgentLoop:
    """构造不访问真实 Provider 的最小 AgentLoop，供兼容委托测试使用。"""
    return AgentLoop(
        client=MagicMock(),
        model="test-model",
        system_prompt="sys",
        registry=ToolRegistry(),
        store=MemoryStore(path=tmp_path / "memory.json", max_history=10),
    )


def test_agent_loop_constructs_model_turn_driver_with_shared_adapter(tmp_path):
    """验证 AgentLoop 只装配 Driver，且 Driver 与旧入口共享同一模型适配器。"""
    loop = _loop(tmp_path)

    assert isinstance(loop.model_turn_driver, ModelTurnDriver)
    assert loop.model_turn_driver.adapter is loop.model_adapter


def test_legacy_complete_once_delegates_to_model_turn_driver(tmp_path):
    """验证旧非流式私有入口保持签名和返回值，防止迁移破坏内部调用者。"""
    loop = _loop(tmp_path)
    expected = ("done", [], False, TokenUsage(total_tokens=3), "stop")
    loop.model_turn_driver = MagicMock()
    loop.model_turn_driver.complete_once.return_value = expected
    messages = [{"role": "user", "content": "hello"}]

    result = loop._complete_once(messages, [])

    assert result == expected
    loop.model_turn_driver.complete_once.assert_called_once_with(messages, [])


def test_legacy_stream_once_delegates_callbacks_without_reordering(tmp_path):
    """验证旧流式入口原样传递消息、工具和回调，防止 UI 事件链在拆分后丢失。"""
    loop = _loop(tmp_path)
    expected = ("streamed", [], False, TokenUsage(), "stop")
    loop.model_turn_driver = MagicMock()
    loop.model_turn_driver.stream_once.return_value = expected
    messages = [{"role": "user", "content": "hello"}]
    tools = [{"type": "function"}]
    on_text = MagicMock()
    on_event = MagicMock()

    result = loop._stream_once(messages, tools, on_text, on_event)

    assert result == expected
    loop.model_turn_driver.stream_once.assert_called_once_with(
        messages,
        tools,
        on_text,
        on_event,
    )
