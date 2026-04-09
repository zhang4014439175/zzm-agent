import json
import pytest
from unittest.mock import MagicMock
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
    
    result = loop.run("Hi")
    
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
    
    result = loop.run("call echo")
    
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
    
    loop.run("new message")
    
    # Inspect the messages sent to the API
    call_args = loop.client.chat.completions.create.call_args
    messages = call_args.kwargs["messages"]
    
    contents = [m["content"] for m in messages if "content" in m and m["content"]]
    assert "previous" in contents
    assert "new message" in contents
    assert "sys" in contents
