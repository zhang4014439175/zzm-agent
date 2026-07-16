from types import SimpleNamespace
from unittest.mock import MagicMock

from zzm_agent.core.agent_loop import AgentLoop
from zzm_agent.core.hooks import HookRegistry, HookResult, HookType
from zzm_agent.core.model_adapter import (
    ModelCapabilities,
    OpenAIChatCompletionsAdapter,
)
from zzm_agent.core.model_stream import ModelStreamEventKind
from zzm_agent.core.query_engine import QueryEngine
from zzm_agent.core.state_serialization import StateSnapshotStore
from zzm_agent.core.tool_registry import ToolRegistry
from zzm_agent.memory.store import MemoryStore


def make_response(content=None, tool_calls=None, finish_reason=None):
    message = SimpleNamespace(content=content, tool_calls=tool_calls or [])
    choice = SimpleNamespace(message=message, finish_reason=finish_reason)
    return SimpleNamespace(choices=[choice], usage=None, error=None)


def make_stream_chunk(
    content=None,
    reasoning=None,
    tool_calls=None,
    usage=None,
    finish_reason=None,
):
    delta = SimpleNamespace(
        content=content,
        reasoning_summary=reasoning,
        tool_calls=tool_calls or [],
    )
    choice = SimpleNamespace(delta=delta, finish_reason=finish_reason)
    return SimpleNamespace(choices=[choice], usage=usage)


def make_tool_call_delta(index, tool_call_id=None, name=None, arguments=None):
    return SimpleNamespace(
        index=index,
        id=tool_call_id,
        function=SimpleNamespace(name=name, arguments=arguments),
    )


def test_openai_adapter_normalizes_response_and_stream_chunks():
    tool_call = SimpleNamespace(
        id="call_1",
        type="function",
        function=SimpleNamespace(name="echo", arguments='{"text":"hi"}'),
    )
    adapter = OpenAIChatCompletionsAdapter(client=MagicMock())

    response = adapter.normalize_response(
        make_response(content="hello", tool_calls=[tool_call], finish_reason="tool_calls")
    )

    assert response.content == "hello"
    assert response.finish_reason == "tool_calls"
    assert response.tool_calls == [
        {
            "id": "call_1",
            "type": "function",
            "function": {"name": "echo", "arguments": '{"text":"hi"}'},
        }
    ]

    chunks = list(
        adapter.iter_stream_chunks(
            [
                make_stream_chunk(reasoning="checking"),
                make_stream_chunk(content="Hel"),
                make_stream_chunk(finish_reason="stop"),
                make_stream_chunk(
                    tool_calls=[
                        make_tool_call_delta(
                            index=0,
                            tool_call_id="call_2",
                            name="ec",
                            arguments='{"te',
                        )
                    ]
                ),
            ]
        )
    )

    assert chunks[0].reasoning_summary == "checking"
    assert chunks[1].content_delta == "Hel"
    assert chunks[2].finish_reason == "stop"
    assert chunks[3].tool_call_deltas[0].tool_call_id == "call_2"
    assert chunks[3].tool_call_deltas[0].name_delta == "ec"
    assert chunks[3].tool_call_deltas[0].arguments_delta == '{"te'


def test_agent_loop_emits_visible_stream_events_without_pseudo_tool_xml(tmp_path):
    registry = ToolRegistry()

    @registry.tool(description="run shell")
    def run_shell(command: str) -> str:
        return f"RAN:{command}"

    store = MemoryStore(path=tmp_path / "memory.json", max_history=10)
    loop = AgentLoop(
        client=MagicMock(),
        model="test-model",
        system_prompt="sys",
        registry=registry,
        store=store,
    )
    first_stream = iter(
        [
            make_stream_chunk(content="我先检查。"),
            make_stream_chunk(content="<tool_call>shell"),
            make_stream_chunk(content="<arg_key>cmd</arg_key>"),
            make_stream_chunk(content="<arg_value>dir</arg_value>"),
            make_stream_chunk(content="</tool_call>"),
        ]
    )
    second_stream = iter([make_stream_chunk(content="完成")])
    loop.client.chat.completions.create.side_effect = [first_stream, second_stream]

    events = []
    reply = loop.run("看看项目", stream=True, on_stream_event=events.append)

    assert reply == "完成"
    content_events = [
        event.text
        for event in events
        if event.kind is ModelStreamEventKind.CONTENT_DELTA
    ]
    assert content_events == ["我先检查。", "完成"]
    assert all("<tool_call>" not in text for text in content_events)
    assert events[-1].kind is ModelStreamEventKind.FINAL_MESSAGE


def test_agent_loop_emits_tool_call_delta_events_for_native_stream(tmp_path):
    registry = ToolRegistry()

    @registry.tool(description="echo")
    def echo(text: str) -> str:
        """返回可预测结果，用于验证一次让出后的自动续段。"""
        return f"ECHO:{text}"

    store = MemoryStore(path=tmp_path / "memory.json", max_history=10)
    loop = AgentLoop(
        client=MagicMock(),
        model="test-model",
        system_prompt="sys",
        registry=registry,
        store=store,
    )
    first_stream = iter(
        [
            make_stream_chunk(
                tool_calls=[
                    make_tool_call_delta(
                        index=0,
                        tool_call_id="call_1",
                        name="ec",
                        arguments='{"te',
                    )
                ]
            ),
            make_stream_chunk(
                tool_calls=[
                    make_tool_call_delta(
                        index=0,
                        name="ho",
                        arguments='xt":"world"}',
                    )
                ]
            ),
        ]
    )
    second_stream = iter([make_stream_chunk(content="Done")])
    loop.client.chat.completions.create.side_effect = [first_stream, second_stream]

    events = []
    reply = loop.run("call echo", stream=True, on_stream_event=events.append)

    assert reply == "Done"
    tool_events = [
        event
        for event in events
        if event.kind is ModelStreamEventKind.TOOL_CALL_DELTA
    ]
    assert [event.arguments_delta for event in tool_events] == ['{"te', 'xt":"world"}']
    assert store.load_history()[1]["tool_calls"][0]["function"]["name"] == "echo"


def test_query_engine_submits_message_and_saves_conversation_snapshot(tmp_path):
    registry = ToolRegistry()
    store = MemoryStore(path=tmp_path / "memory.json", max_history=10, session_id="s1")
    loop = AgentLoop(
        client=MagicMock(),
        model="test-model",
        system_prompt="sys",
        registry=registry,
        store=store,
    )
    loop.client.chat.completions.create.return_value = make_response(
        content="Hello!",
        finish_reason="stop",
    )
    snapshot_store = StateSnapshotStore(tmp_path / "conversation.json")
    engine = QueryEngine(
        agent_loop=loop,
        snapshot_store=snapshot_store,
    )

    result = engine.submit_message("Hi", stream=False)
    envelope = snapshot_store.load_envelope()

    assert result.reply == "Hello!"
    assert result.turn is not None
    assert result.turn.final_response == "Hello!"
    assert result.turn.termination is not None
    assert result.turn.termination.reason == "model_completed"
    assert result.turn.termination.provider_finish_reason == "stop"
    assert result.events[-1].kind is ModelStreamEventKind.TERMINATION
    assert envelope is not None
    assert envelope.state_type == "conversation"
    assert envelope.metadata["reason"] == "turn.completed"
    assert envelope.payload["session_id"] == "s1"
    assert envelope.payload["active_turn"]["final_response"] == "Hello!"
    assert store.load_history()[-1]["content"] == "Hello!"


def test_query_engine_persists_and_emits_empty_response_block(tmp_path):
    registry = ToolRegistry()
    store = MemoryStore(path=tmp_path / "memory.json", max_history=10, session_id="s1")
    loop = AgentLoop(
        client=MagicMock(),
        model="test-model",
        system_prompt="sys",
        registry=registry,
        store=store,
        empty_final_retries=1,
    )
    loop.client.chat.completions.create.side_effect = [
        make_response(content="", finish_reason="stop"),
        make_response(content="", finish_reason="stop"),
    ]
    snapshot_store = StateSnapshotStore(tmp_path / "conversation.json")
    engine = QueryEngine(agent_loop=loop, snapshot_store=snapshot_store)

    result = engine.submit_message("do work", stream=False)
    envelope = snapshot_store.load_envelope()

    assert result.turn is not None
    assert result.turn.status.value == "blocked"
    assert result.turn.termination is not None
    assert result.turn.termination.reason == "empty_model_response"
    assert result.turn.termination.recovery_attempts == 1
    assert envelope is not None
    assert envelope.metadata["reason"] == "turn.blocked"
    assert result.events[-1].kind is ModelStreamEventKind.TERMINATION
    assert result.events[-1].metadata["reason"] == "empty_model_response"


def test_query_engine_auto_continues_yielded_segment(tmp_path):
    """验证内部 Segment 让出后会自动续跑，并且最终只发出一次完成终止事件。"""
    registry = ToolRegistry()

    @registry.tool(description="echo")
    def echo(text: str) -> str:
        """持续返回相同结果，用于制造达到自动续段保险丝的场景。"""
        return f"ECHO:{text}"

    store = MemoryStore(path=tmp_path / "memory.json", max_history=20, session_id="s1")
    loop = AgentLoop(
        client=MagicMock(),
        model="test-model",
        system_prompt="sys",
        registry=registry,
        store=store,
        max_tool_iterations=1,
    )
    tool_call = SimpleNamespace(
        id="call_1",
        type="function",
        function=SimpleNamespace(name="echo", arguments='{"text":"world"}'),
    )
    loop.client.chat.completions.create.side_effect = [
        make_response(tool_calls=[tool_call], finish_reason="tool_calls"),
        make_response(content="all done", finish_reason="stop"),
    ]
    engine = QueryEngine(
        agent_loop=loop,
        config={"agent": {"max_auto_continuations": 3}},
    )

    result = engine.submit_message("complete the task", stream=False)

    assert result.reply == "all done"
    assert [segment.status.value for segment in result.segments] == [
        "yielded",
        "completed",
    ]
    assert loop.client.chat.completions.create.call_count == 2
    assert any(
        event.kind is ModelStreamEventKind.STATUS
        and event.text == "segment.yielded"
        for event in result.events
    )
    termination_events = [
        event
        for event in result.events
        if event.kind is ModelStreamEventKind.TERMINATION
    ]
    assert len(termination_events) == 1
    assert termination_events[0].metadata["status"] == "completed"
    second_messages = loop.client.chat.completions.create.call_args_list[1].kwargs[
        "messages"
    ]
    assert any(
        "CONTINUE_TASK_FROM_CHECKPOINT" in str(message.get("content") or "")
        for message in second_messages
    )


def test_query_engine_blocks_when_auto_continuation_fuse_is_exhausted(tmp_path):
    """验证连续让出达到保险丝上限时明确阻塞，并保留完整分段状态序列。"""
    registry = ToolRegistry()

    @registry.tool(description="echo")
    def echo(text: str) -> str:
        return f"ECHO:{text}"

    store = MemoryStore(path=tmp_path / "memory.json", max_history=30, session_id="s1")
    loop = AgentLoop(
        client=MagicMock(),
        model="test-model",
        system_prompt="sys",
        registry=registry,
        store=store,
        max_tool_iterations=1,
    )
    tool_call = SimpleNamespace(
        id="call_repeat",
        type="function",
        function=SimpleNamespace(name="echo", arguments='{"text":"again"}'),
    )
    loop.client.chat.completions.create.return_value = make_response(
        tool_calls=[tool_call],
        finish_reason="tool_calls",
    )
    engine = QueryEngine(
        agent_loop=loop,
        config={"agent": {"max_auto_continuations": 2}},
    )

    result = engine.submit_message("keep working", stream=False)

    assert result.turn is not None
    assert result.turn.status.value == "blocked"
    assert result.turn.termination is not None
    assert result.turn.termination.reason == "auto_continuation_limit"
    assert "已连续自动续段 2 次" in result.reply
    assert [segment.status.value for segment in result.segments] == [
        "yielded",
        "yielded",
        "blocked",
    ]


def test_query_engine_simple_reply_has_no_continuation_overhead(tmp_path):
    """验证简单回复只调用模型一次，不因自动续段功能增加额外执行开销。"""
    registry = ToolRegistry()
    store = MemoryStore(path=tmp_path / "memory.json", max_history=10, session_id="s1")
    loop = AgentLoop(
        client=MagicMock(),
        model="test-model",
        system_prompt="sys",
        registry=registry,
        store=store,
    )
    loop.client.chat.completions.create.return_value = make_response(
        content="simple",
        finish_reason="stop",
    )
    engine = QueryEngine(agent_loop=loop)

    result = engine.submit_message("hi", stream=False)

    assert result.reply == "simple"
    assert len(result.segments) == 1
    assert result.segments[0].status.value == "completed"
    assert loop.client.chat.completions.create.call_count == 1
    assert not any(event.text == "segment.yielded" for event in result.events)


def test_context_budget_reports_provider_prompt_cache_strategy(tmp_path):
    """验证支持原生 Prompt Cache 的 Provider 会在预算诊断中报告对应策略。"""
    registry = ToolRegistry()
    store = MemoryStore(path=tmp_path / "memory.json", max_history=10)
    client = MagicMock()
    client.chat.completions.create.return_value = make_response(content="ok")
    adapter = OpenAIChatCompletionsAdapter(
        client,
        capabilities=ModelCapabilities(supports_prompt_cache=True),
    )
    loop = AgentLoop(
        client=client,
        model="test-model",
        system_prompt="sys",
        registry=registry,
        store=store,
        model_adapter=adapter,
    )

    assert loop.run("hi", stream=False) == "ok"
    assert loop.last_context_window["prompt_cache_strategy"] == "provider_native"
    assert loop.last_context_window["budget"]["prompt_cache_strategy"] == "provider_native"


def test_query_engine_completion_gate_rejects_empty_completed_reply(tmp_path):
    """验证完成门禁拒绝空最终答复，并把伪完成转换为带原因的阻塞状态。"""
    hooks = HookRegistry()
    hooks.register(
        HookType.STOP,
        lambda _context: HookResult.modify_response(""),
    )
    registry = ToolRegistry()
    store = MemoryStore(path=tmp_path / "memory.json", max_history=10, session_id="s1")
    loop = AgentLoop(
        client=MagicMock(),
        model="test-model",
        system_prompt="sys",
        registry=registry,
        store=store,
        hook_registry=hooks,
    )
    loop.client.chat.completions.create.return_value = make_response(content="draft")
    engine = QueryEngine(agent_loop=loop)

    result = engine.submit_message("answer", stream=False)

    assert result.turn is not None
    assert result.turn.status.value == "blocked"
    assert result.turn.termination is not None
    assert result.turn.termination.reason == "completion_gate_empty_reply"
    assert "完成协议不完整" in result.reply
    assert result.segments[-1].status.value == "blocked"


def test_query_engine_injects_response_language_instruction(tmp_path):
    registry = ToolRegistry()
    store = MemoryStore(path=tmp_path / "memory.json", max_history=10, session_id="s1")
    loop = AgentLoop(
        client=MagicMock(),
        model="test-model",
        system_prompt="sys",
        registry=registry,
        store=store,
    )
    loop.client.chat.completions.create.return_value = make_response(content="好的")
    engine = QueryEngine(
        agent_loop=loop,
        config={"ui": {"response_language": "zh-CN"}},
    )

    result = engine.submit_message(
        "Review the current working tree",
        stream=False,
        language_input="/review",
    )
    messages = loop.client.chat.completions.create.call_args.kwargs["messages"]

    assert result.response_language is not None
    assert result.response_language.language == "zh-CN"
    assert result.response_language.source == "config"
    assert any(
        message["role"] == "system" and "简体中文" in message["content"]
        for message in messages
    )
    assert not any("简体中文" in message["content"] for message in store.load_history())
