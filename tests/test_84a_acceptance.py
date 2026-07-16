from types import SimpleNamespace
from unittest.mock import MagicMock

from zzm_agent.core.agent_loop import AgentLoop
from zzm_agent.core.model_stream import ModelStreamEventKind
from zzm_agent.core.query_engine import QueryEngine
from zzm_agent.core.tool_registry import ToolRegistry
from zzm_agent.memory.store import MemoryStore


def _response(content=None, tool_calls=None, finish_reason=None):
    """构造最小 Provider 响应，便于精确控制文本、工具调用和结束原因。"""
    message = SimpleNamespace(content=content, tool_calls=tool_calls or [])
    choice = SimpleNamespace(message=message, finish_reason=finish_reason)
    return SimpleNamespace(choices=[choice], usage=None, error=None)


def _tool_call(call_id: str, name: str, arguments: str = "{}"):
    """构造 OpenAI 兼容工具调用对象，供阶段验收场景复用。"""
    return SimpleNamespace(
        id=call_id,
        type="function",
        function=SimpleNamespace(name=name, arguments=arguments),
    )


def test_long_tool_task_artifactizes_compacts_and_auto_continues(tmp_path):
    """阶段验收：长工具输出经 Artifact 化和历史压缩后仍能自动续段完成。"""
    registry = ToolRegistry()
    full_output = "source-line: useful evidence\n" * 1200

    @registry.tool(description="collect a large report")
    def collect_report() -> str:
        """返回大型报告全文，以触发 Artifact 化和上下文压缩。"""
        return full_output

    store = MemoryStore(
        path=tmp_path / "memory.json",
        max_history=50,
        max_context_tokens=500,
        compression_keep_recent=1,
    )
    loop = AgentLoop(
        client=MagicMock(),
        model="test-model",
        system_prompt="sys",
        registry=registry,
        store=store,
        max_tool_iterations=1,
        max_inline_tool_result_tokens=20,
    )
    loop.client.chat.completions.create.side_effect = [
        _response(
            tool_calls=[_tool_call("call_report", "collect_report")],
            finish_reason="tool_calls",
        ),
        _response(content="任务完成，证据已经检查。", finish_reason="stop"),
    ]
    engine = QueryEngine(
        agent_loop=loop,
        config={"agent": {"max_auto_continuations": 3}},
    )

    result = engine.submit_message("收集报告并给出结论", stream=False)

    assert result.reply == "任务完成，证据已经检查。"
    assert [segment.status.value for segment in result.segments] == [
        "yielded",
        "completed",
    ]
    assert result.segments[0].checkpoint["artifacts"]
    artifact_id = result.segments[0].checkpoint["artifacts"][0]["artifact_id"]
    assert loop.artifact_store.read_text(artifact_id) == full_output
    yielded_event = next(
        event
        for event in result.events
        if event.kind is ModelStreamEventKind.STATUS
        and event.text == "segment.yielded"
    )
    assert yielded_event.metadata["compression_applied"] is True
    assert loop.client.chat.completions.create.call_count == 2
    second_messages = loop.client.chat.completions.create.call_args_list[1].kwargs[
        "messages"
    ]
    assert any(
        "Runtime compression summary" in str(message.get("content") or "")
        for message in second_messages
    )


def test_provider_truncation_empty_reply_recovers_in_same_segment(tmp_path):
    """阶段验收：Provider 截断造成的空回复可在有限恢复后正常完成并留痕。"""
    registry = ToolRegistry()
    store = MemoryStore(path=tmp_path / "memory.json", max_history=10)
    loop = AgentLoop(
        client=MagicMock(),
        model="test-model",
        system_prompt="sys",
        registry=registry,
        store=store,
        empty_final_retries=1,
    )
    loop.client.chat.completions.create.side_effect = [
        _response(content="", finish_reason="length"),
        _response(content="恢复后的完整答复", finish_reason="stop"),
    ]
    engine = QueryEngine(agent_loop=loop)

    result = engine.submit_message("给出完整答复", stream=False)

    assert result.reply == "恢复后的完整答复"
    assert len(result.segments) == 1
    assert result.turn is not None
    assert result.turn.provider_finish_reason_history == ["length", "stop"]
    assert result.turn.status.value == "completed"


def test_repeated_tool_cycle_still_blocks_instead_of_auto_continuing(tmp_path):
    """阶段验收：真实重复工具循环仍会阻塞，不被自动续段机制无限放大。"""
    registry = ToolRegistry()

    @registry.tool(description="echo")
    def echo(text: str) -> str:
        """返回稳定 Observation，用于验证重复循环仍会被明确阻塞。"""
        return f"ECHO:{text}"

    store = MemoryStore(path=tmp_path / "memory.json", max_history=20)
    call = _tool_call("call_same", "echo", '{"text":"same"}')
    loop = AgentLoop(
        client=MagicMock(),
        model="test-model",
        system_prompt="sys",
        registry=registry,
        store=store,
        max_tool_iterations=20,
        duplicate_tool_call_limit=2,
    )
    loop.client.chat.completions.create.return_value = _response(tool_calls=[call])
    engine = QueryEngine(agent_loop=loop)

    result = engine.submit_message("不要重复调用", stream=False)

    assert result.turn is not None
    assert result.turn.status.value == "blocked"
    assert len(result.segments) == 1
    assert "repeated" in result.reply.lower()
