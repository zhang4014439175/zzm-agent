import json
import pytest
from unittest.mock import MagicMock
from types import SimpleNamespace
from zzm_agent.core.agent_loop import AgentLoop
from zzm_agent.core.tool_registry import ToolRegistry
from zzm_agent.memory.store import MemoryStore


@pytest.fixture
def registry():
    """Fixture to provide a ToolRegistry with an 'echo' tool."""
    r = ToolRegistry()

    @r.tool(description="返回固定字符串")
    def echo(text: str) -> str:
        return f"ECHO:{text}"

    return r


@pytest.fixture
def store(tmp_path):
    """Fixture to provide a MemoryStore in a temporary directory."""
    return MemoryStore(path=tmp_path / "memory.json", max_history=10)


def make_response(content=None, tool_calls=None):
    """Helper to create a mock OpenAI chat completion response."""
    msg = MagicMock()
    msg.content = content
    msg.tool_calls = tool_calls
    # Ensure role is set for history persistence simulation
    msg.role = "assistant"
    
    choice = MagicMock()
    choice.message = msg
    
    resp = MagicMock()
    resp.choices = [choice]
    return resp


def make_stream_chunk(content=None, tool_calls=None):
    delta = SimpleNamespace(
        content=content,
        tool_calls=tool_calls or [],
    )
    choice = SimpleNamespace(delta=delta)
    return SimpleNamespace(choices=[choice])


def make_tool_call_delta(index, tool_call_id=None, name=None, arguments=None):
    return SimpleNamespace(
        index=index,
        id=tool_call_id,
        function=SimpleNamespace(name=name, arguments=arguments),
    )


def test_simple_reply(registry, store):
    """Test a basic user-assistant exchange without tool calls."""
    loop = AgentLoop(
        client=MagicMock(),
        model="test-model",
        system_prompt="You are helpful.",
        registry=registry,
        store=store,
    )
    # Mock the API to return a direct string content
    loop.client.chat.completions.create.return_value = make_response(content="Hello!")
    
    result = loop.run("Hi", stream=False)
    
    assert result == "Hello!"
    # Verify history was saved: 1 user msg + 1 assistant msg
    assert len(store.load_history()) == 2
    assert store.load_history()[-1]["content"] == "Hello!"


def test_tool_call_then_reply(registry, store):
    """Test the loop when the model requests a tool call before responding."""
    # Mock tool call object
    tool_call = MagicMock()
    tool_call.id = "call_1"
    tool_call.function.name = "echo"
    tool_call.function.arguments = json.dumps({"text": "world"})

    loop = AgentLoop(
        client=MagicMock(),
        model="test-model",
        system_prompt="You are helpful.",
        registry=registry,
        store=store,
    )
    
    # First call returns tool_call, second call returns final text
    loop.client.chat.completions.create.side_effect = [
        make_response(tool_calls=[tool_call]),
        make_response(content="Done!"),
    ]
    
    result = loop.run("call echo", stream=False)
    
    assert result == "Done!"
    assert loop.client.chat.completions.create.call_count == 2
    
    # Check history: User, Assistant (tool_call), Tool (result), Assistant (final)
    history = store.load_history()
    assert len(history) == 4
    assert history[2]["role"] == "tool"
    assert history[2]["content"] == "ECHO:world"


def test_history_loaded_on_run(registry, store):
    """Test that existing history is included in the prompt for context."""
    # Pre-populate history
    store.append([{"role": "user", "content": "previous"}])
    
    loop = AgentLoop(
        client=MagicMock(),
        model="test-model",
        system_prompt="sys",
        registry=registry,
        store=store,
    )
    loop.client.chat.completions.create.return_value = make_response(content="ok")
    
    loop.run("new message", stream=False)
    
    # Inspect the messages sent to the API
    call_args = loop.client.chat.completions.create.call_args
    messages = call_args.kwargs["messages"]
    
    contents = [m["content"] for m in messages if "content" in m and m["content"]]
    assert "previous" in contents
    assert "new message" in contents
    assert "sys" in contents


def test_stream_simple_reply(registry, store):
    loop = AgentLoop(
        client=MagicMock(),
        model="test-model",
        system_prompt="You are helpful.",
        registry=registry,
        store=store,
    )
    loop.client.chat.completions.create.return_value = iter(
        [
            make_stream_chunk(content="Hel"),
            make_stream_chunk(content="lo!"),
        ]
    )

    chunks = []
    result = loop.run("Hi", stream=True, on_text_chunk=chunks.append)

    assert result == "Hello!"
    assert chunks == ["Hel", "lo!"]
    assert len(store.load_history()) == 2
    assert store.load_history()[-1]["content"] == "Hello!"
    assert loop.client.chat.completions.create.call_args.kwargs["stream"] is True


def test_stream_tool_call_then_reply(registry, store):
    loop = AgentLoop(
        client=MagicMock(),
        model="test-model",
        system_prompt="You are helpful.",
        registry=registry,
        store=store,
    )

    first_stream = iter(
        [
            make_stream_chunk(
                tool_calls=[
                    make_tool_call_delta(index=0, tool_call_id="call_1", name="ec", arguments='{"te')
                ]
            ),
            make_stream_chunk(
                tool_calls=[
                    make_tool_call_delta(index=0, name="ho", arguments='xt":"world"}')
                ]
            ),
        ]
    )
    second_stream = iter(
        [
            make_stream_chunk(content="Do"),
            make_stream_chunk(content="ne!"),
        ]
    )
    loop.client.chat.completions.create.side_effect = [first_stream, second_stream]

    chunks = []
    result = loop.run("call echo", stream=True, on_text_chunk=chunks.append)

    assert result == "Done!"
    assert chunks == ["Do", "ne!"]
    assert loop.client.chat.completions.create.call_count == 2

    history = store.load_history()
    assert len(history) == 4
    assert history[1]["tool_calls"][0]["function"]["name"] == "echo"
    assert history[1]["tool_calls"][0]["function"]["arguments"] == '{"text":"world"}'
    assert history[2]["role"] == "tool"
    assert history[2]["content"] == "ECHO:world"


def test_stream_interruption_returns_partial_text_without_persisting(registry, store):
    loop = AgentLoop(
        client=MagicMock(),
        model="test-model",
        system_prompt="You are helpful.",
        registry=registry,
        store=store,
    )

    def interrupted_stream():
        yield make_stream_chunk(content="Par")
        raise KeyboardInterrupt

    loop.client.chat.completions.create.return_value = interrupted_stream()

    chunks = []
    result = loop.run("Hi", stream=True, on_text_chunk=chunks.append)

    assert result == "Par"
    assert chunks == ["Par"]
    assert store.load_history() == []
