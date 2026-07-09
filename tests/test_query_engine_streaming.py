from types import SimpleNamespace
from unittest.mock import MagicMock

from zzm_agent.core.agent_loop import AgentLoop
from zzm_agent.core.model_adapter import OpenAIChatCompletionsAdapter
from zzm_agent.core.model_stream import ModelStreamEventKind
from zzm_agent.core.query_engine import QueryEngine
from zzm_agent.core.state_serialization import StateSnapshotStore
from zzm_agent.core.tool_registry import ToolRegistry
from zzm_agent.memory.store import MemoryStore


def make_response(content=None, tool_calls=None):
    message = SimpleNamespace(content=content, tool_calls=tool_calls or [])
    choice = SimpleNamespace(message=message)
    return SimpleNamespace(choices=[choice], usage=None, error=None)


def make_stream_chunk(content=None, reasoning=None, tool_calls=None, usage=None):
    delta = SimpleNamespace(
        content=content,
        reasoning_summary=reasoning,
        tool_calls=tool_calls or [],
    )
    choice = SimpleNamespace(delta=delta)
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
        make_response(content="hello", tool_calls=[tool_call])
    )

    assert response.content == "hello"
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
    assert chunks[2].tool_call_deltas[0].tool_call_id == "call_2"
    assert chunks[2].tool_call_deltas[0].name_delta == "ec"
    assert chunks[2].tool_call_deltas[0].arguments_delta == '{"te'


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
    loop.client.chat.completions.create.return_value = make_response(content="Hello!")
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
    assert envelope is not None
    assert envelope.state_type == "conversation"
    assert envelope.metadata["reason"] == "turn.completed"
    assert envelope.payload["session_id"] == "s1"
    assert envelope.payload["active_turn"]["final_response"] == "Hello!"
    assert store.load_history()[-1]["content"] == "Hello!"


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
