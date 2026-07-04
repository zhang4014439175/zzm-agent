import json
import pytest
import uuid
from pathlib import Path
from unittest.mock import MagicMock
from types import SimpleNamespace
from zzm_agent.core.agent_loop import AgentLoop
from zzm_agent.core.observability import ToolEventLogger, read_tool_event_log
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
    resp.usage = None
    resp.error = None
    return resp


def make_response_with_usage(content=None, tool_calls=None, prompt=0, completion=0):
    resp = make_response(content=content, tool_calls=tool_calls)
    resp.usage = SimpleNamespace(
        prompt_tokens=prompt,
        completion_tokens=completion,
        total_tokens=prompt + completion,
    )
    return resp


def make_error_response(message="Internal Server Error", code=500):
    return SimpleNamespace(
        choices=None,
        usage=None,
        error={"message": message, "code": code},
    )


def make_error_test_store(session_id: str):
    return MemoryStore(
        path=Path(".tmp_agent_loop_api_error") / "memory.json",
        max_history=10,
        session_id=f"{session_id}-{uuid.uuid4().hex}",
    )


def make_stream_chunk(content=None, tool_calls=None):
    # Match the minimal streamed SDK shape consumed by AgentLoop._stream_once.
    delta = SimpleNamespace(
        content=content,
        tool_calls=tool_calls or [],
    )
    choice = SimpleNamespace(delta=delta)
    return SimpleNamespace(choices=[choice])


def make_tool_call_delta(index, tool_call_id=None, name=None, arguments=None):
    # Streamed tool calls may arrive over multiple chunks keyed by the same index.
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


def test_agent_loop_tracks_api_token_usage(registry, store):
    loop = AgentLoop(
        client=MagicMock(),
        model="test-model",
        system_prompt="You are helpful.",
        registry=registry,
        store=store,
    )
    loop.client.chat.completions.create.return_value = make_response_with_usage(
        content="Hello!",
        prompt=12,
        completion=5,
    )

    result = loop.run("Hi", stream=False)

    assert result == "Hello!"
    assert loop.last_turn_usage.prompt_tokens == 12
    assert loop.last_turn_usage.completion_tokens == 5
    assert loop.last_turn_usage.total_tokens == 17
    assert loop.last_turn_usage.source == "api"
    assert loop.cumulative_usage.total_tokens == 17


def test_chat_completion_error_payload_raises_clear_error(registry):
    store = make_error_test_store("api-error-payload")
    loop = AgentLoop(
        client=MagicMock(),
        model="test-model",
        system_prompt="You are helpful.",
        registry=registry,
        store=store,
    )
    loop.client.chat.completions.create.return_value = make_error_response()

    with pytest.raises(RuntimeError, match="Internal Server Error .*500"):
        loop.run("Hi", stream=False)

    assert store.load_history() == []


def test_chat_completion_without_choices_raises_clear_error(registry):
    store = make_error_test_store("missing-choices")
    loop = AgentLoop(
        client=MagicMock(),
        model="test-model",
        system_prompt="You are helpful.",
        registry=registry,
        store=store,
    )
    loop.client.chat.completions.create.return_value = SimpleNamespace(
        choices=None,
        usage=None,
        error=None,
    )

    with pytest.raises(RuntimeError, match="did not include choices"):
        loop.run("Hi", stream=False)

    assert store.load_history() == []


def test_agent_loop_estimates_token_usage_when_provider_omits_usage(registry, store):
    loop = AgentLoop(
        client=MagicMock(),
        model="test-model",
        system_prompt="You are helpful.",
        registry=registry,
        store=store,
    )
    loop.client.chat.completions.create.return_value = make_response(content="Hello!")

    loop.run("Hi", stream=False)

    assert loop.last_turn_usage.total_tokens > 0
    assert loop.last_turn_usage.source == "estimated"


def test_agent_loop_exposes_context_window_metadata(registry, store):
    loop = AgentLoop(
        client=MagicMock(),
        model="test-model",
        system_prompt="You are helpful.",
        registry=registry,
        store=store,
    )
    loop.client.chat.completions.create.return_value = make_response(content="Hello!")

    loop.run("Hi", stream=False)

    assert loop.last_context_window["max_context_tokens"] == store.max_context_tokens
    assert loop.last_context_window["total_tokens"] > 0
    assert loop.last_context_window["message_tokens"] > 0
    assert loop.last_context_window["tool_schema_tokens"] > 0
    assert (
        loop.last_context_window["total_tokens"]
        == loop.last_context_window["message_tokens"]
        + loop.last_context_window["tool_schema_tokens"]
    )


def test_agent_loop_saves_latest_context_snapshot(registry, store):
    loop = AgentLoop(
        client=MagicMock(),
        model="test-model",
        system_prompt="You are helpful.",
        registry=registry,
        store=store,
    )
    loop.client.chat.completions.create.return_value = make_response(content="Hello!")

    loop.run("Hi", stream=False)

    snapshot = json.loads(store.latest_context_path.read_text(encoding="utf-8"))
    assert snapshot["session_id"] == store.session_id
    assert snapshot["model"] == "test-model"
    assert snapshot["latest_user_input"] == "Hi"
    assert snapshot["stream"] is False
    assert snapshot["tool_iteration"] == 0
    assert snapshot["request"]["model"] == "test-model"
    assert snapshot["request"]["messages"][0] == {
        "role": "system",
        "content": "You are helpful.",
    }
    assert snapshot["request"]["messages"][-1] == {"role": "user", "content": "Hi"}
    assert snapshot["request"]["tools"]
    assert snapshot["context_window"]["total_tokens"] == loop.last_context_window["total_tokens"]


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


def test_text_tool_call_fallback_executes_shell_alias(store):
    registry = ToolRegistry()

    @registry.tool(description="run shell")
    def run_shell(command: str) -> str:
        return f"RAN:{command}"

    loop = AgentLoop(
        client=MagicMock(),
        model="test-model",
        system_prompt="sys",
        registry=registry,
        store=store,
    )
    loop.client.chat.completions.create.side_effect = [
        make_response(
            content=(
                "<tool_call>shell\n"
                "<arg_key>cmd</arg_key>\n"
                "<arg_value>Get-ChildItem -Path E:\\PythonProject\\study\\EatToday "
                "-Recurse -File</arg_value>\n"
                "</tool_call>"
            )
        ),
        make_response(content="检查完成"),
    ]

    result = loop.run("看看项目问题", stream=False)

    assert result == "检查完成"
    assert loop.client.chat.completions.create.call_count == 2
    history = store.load_history()
    assert history[1]["role"] == "assistant"
    assert history[1]["content"] is None
    assert history[1]["tool_calls"][0]["function"]["name"] == "run_shell"
    assert json.loads(history[1]["tool_calls"][0]["function"]["arguments"]) == {
        "command": "Get-ChildItem -Path E:\\PythonProject\\study\\EatToday -Recurse -File"
    }
    assert history[2]["role"] == "tool"
    assert history[2]["content"].startswith("RAN:Get-ChildItem")


def test_text_tool_call_fallback_executes_after_preamble(store):
    registry = ToolRegistry()

    @registry.tool(description="run shell")
    def run_shell(command: str) -> str:
        return f"RAN:{command}"

    loop = AgentLoop(
        client=MagicMock(),
        model="test-model",
        system_prompt="sys",
        registry=registry,
        store=store,
    )
    loop.client.chat.completions.create.side_effect = [
        make_response(
            content=(
                "好的，我来帮您查看项目结构。首先，我会列出项目中的所有文件。"
                "<tool_call>shell"
                "<arg_key>cmd</arg_key>"
                "<arg_value>Get-ChildItem -Path E:\\PythonProject\\study\\EatToday "
                "-Recurse -File | Select-Object FullName</arg_value>"
                "</tool_call>"
            )
        ),
        make_response(content="项目结构如下"),
    ]

    result = loop.run("你好，请帮我看看现在的项目结构", stream=False)

    assert result == "项目结构如下"
    history = store.load_history()
    assert len(history) == 4
    assert history[1]["content"] is None
    assert history[1]["tool_calls"][0]["function"]["name"] == "run_shell"
    assert json.loads(history[1]["tool_calls"][0]["function"]["arguments"]) == {
        "command": (
            "Get-ChildItem -Path E:\\PythonProject\\study\\EatToday "
            "-Recurse -File | Select-Object FullName"
        )
    }
    assert history[2]["content"].startswith("RAN:Get-ChildItem")


def test_native_tool_calls_take_precedence_over_text_tool_call_fallback(registry, store):
    native_call = MagicMock()
    native_call.id = "call_native"
    native_call.function.name = "echo"
    native_call.function.arguments = json.dumps({"text": "native"})

    loop = AgentLoop(
        client=MagicMock(),
        model="test-model",
        system_prompt="sys",
        registry=registry,
        store=store,
    )
    loop.client.chat.completions.create.side_effect = [
        make_response(
            content=(
                "<tool_call>shell\n"
                "<arg_key>cmd</arg_key><arg_value>ignored</arg_value>\n"
                "</tool_call>"
            ),
            tool_calls=[native_call],
        ),
        make_response(content="done"),
    ]

    assert loop.run("call echo", stream=False) == "done"
    history = store.load_history()
    assert history[1]["tool_calls"][0]["function"]["name"] == "echo"
    assert history[2]["content"] == "ECHO:native"


def test_stream_text_tool_call_fallback_does_not_render_pseudo_xml(store):
    registry = ToolRegistry()

    @registry.tool(description="run shell")
    def run_shell(command: str) -> str:
        return f"RAN:{command}"

    loop = AgentLoop(
        client=MagicMock(),
        model="test-model",
        system_prompt="sys",
        registry=registry,
        store=store,
    )
    first_stream = iter(
        [
            make_stream_chunk(content="<tool"),
            make_stream_chunk(content="_call>shell\n"),
            make_stream_chunk(content="<arg_key>cmd</arg_key>"),
            make_stream_chunk(content="<arg_value>dir</arg_value>"),
            make_stream_chunk(content="</tool_call>"),
        ]
    )
    second_stream = iter([make_stream_chunk(content="完成")])
    loop.client.chat.completions.create.side_effect = [first_stream, second_stream]

    chunks = []
    result = loop.run("看看项目", stream=True, on_text_chunk=chunks.append)

    assert result == "完成"
    assert chunks == ["完成"]
    history = store.load_history()
    assert history[1]["tool_calls"][0]["function"]["name"] == "run_shell"
    assert history[2]["content"] == "RAN:dir"


def test_stream_text_tool_call_after_preamble_hides_pseudo_xml(store):
    registry = ToolRegistry()

    @registry.tool(description="run shell")
    def run_shell(command: str) -> str:
        return f"RAN:{command}"

    loop = AgentLoop(
        client=MagicMock(),
        model="test-model",
        system_prompt="sys",
        registry=registry,
        store=store,
    )
    first_stream = iter(
        [
            make_stream_chunk(content="好的，我来帮您查看项目结构。"),
            make_stream_chunk(content="<tool_call>shell"),
            make_stream_chunk(content="<arg_key>cmd</arg_key>"),
            make_stream_chunk(content="<arg_value>dir</arg_value>"),
            make_stream_chunk(content="</tool_call>"),
        ]
    )
    second_stream = iter([make_stream_chunk(content="完成")])
    loop.client.chat.completions.create.side_effect = [first_stream, second_stream]

    chunks = []
    result = loop.run("看看项目", stream=True, on_text_chunk=chunks.append)

    assert result == "完成"
    assert chunks == ["好的，我来帮您查看项目结构。", "完成"]
    history = store.load_history()
    assert history[1]["tool_calls"][0]["function"]["name"] == "run_shell"
    assert history[2]["content"] == "RAN:dir"


def test_tool_callbacks_receive_start_and_end_events(registry, store):
    tool_call = MagicMock()
    tool_call.id = "call_1"
    tool_call.function.name = "echo"
    tool_call.function.arguments = json.dumps({"text": "world"})
    events = []

    loop = AgentLoop(
        client=MagicMock(),
        model="test-model",
        system_prompt="sys",
        registry=registry,
        store=store,
        on_tool_start=events.append,
        on_tool_end=events.append,
        on_tool_error=events.append,
    )
    loop.client.chat.completions.create.side_effect = [
        make_response(tool_calls=[tool_call]),
        make_response(content="Done!"),
    ]

    result = loop.run("call echo", stream=False)

    assert result == "Done!"
    assert [event.event_name for event in events] == ["tool.start", "tool.end"]
    assert events[0].tool_name == "echo"
    assert events[0].arguments_summary == {"text": "world"}
    assert events[1].status == "success"
    assert events[1].duration_ms >= 0
    assert events[1].result_preview == "ECHO:world"


def test_tool_callbacks_receive_error_events(tmp_path):
    registry = ToolRegistry()

    @registry.tool(description="explode")
    def explode(value: str) -> str:
        raise ValueError(f"bad value: {value}")

    store = MemoryStore(path=tmp_path / "memory.json", max_history=10)
    tool_call = MagicMock()
    tool_call.id = "call_1"
    tool_call.function.name = "explode"
    tool_call.function.arguments = json.dumps({"value": "x"})
    errors = []

    loop = AgentLoop(
        client=MagicMock(),
        model="test-model",
        system_prompt="sys",
        registry=registry,
        store=store,
        on_tool_error=errors.append,
    )
    loop.client.chat.completions.create.side_effect = [
        make_response(tool_calls=[tool_call]),
        make_response(content="handled"),
    ]

    assert loop.run("explode", stream=False) == "handled"
    assert len(errors) == 1
    assert errors[0].event_name == "tool.error"
    assert errors[0].tool_name == "explode"
    assert errors[0].error_type == "ValueError"
    assert "bad value" in errors[0].error_message


def test_tool_event_logger_writes_jsonl(registry, store, tmp_path):
    tool_call = MagicMock()
    tool_call.id = "call_1"
    tool_call.function.name = "echo"
    tool_call.function.arguments = json.dumps({"text": "logged"})
    log_path = tmp_path / "tool_events.jsonl"
    logger = ToolEventLogger(log_path)

    loop = AgentLoop(
        client=MagicMock(),
        model="test-model",
        system_prompt="sys",
        registry=registry,
        store=store,
        on_tool_start=logger,
        on_tool_end=logger,
    )
    loop.client.chat.completions.create.side_effect = [
        make_response(tool_calls=[tool_call]),
        make_response(content="Done!"),
    ]

    loop.run("call echo", stream=False)

    records = read_tool_event_log(log_path)
    assert [record["event_name"] for record in records] == ["tool.start", "tool.end"]
    assert records[0]["tool_name"] == "echo"
    assert records[0]["arguments_summary"] == {"text": "logged"}
    assert records[1]["status"] == "success"


def test_agent_loop_stops_at_max_tool_iterations(registry, store):
    tool_call = MagicMock()
    tool_call.id = "call_1"
    tool_call.function.name = "echo"
    tool_call.function.arguments = json.dumps({"text": "again"})

    loop = AgentLoop(
        client=MagicMock(),
        model="test-model",
        system_prompt="sys",
        registry=registry,
        store=store,
        max_tool_iterations=1,
    )
    loop.client.chat.completions.create.return_value = make_response(tool_calls=[tool_call])

    result = loop.run("repeat", stream=False)

    assert "maximum tool iteration limit" in result
    assert loop.client.chat.completions.create.call_count == 1
    assert store.load_history()[-1]["content"] == result


def test_agent_loop_stops_repeated_identical_tool_calls(registry, store):
    tool_call = MagicMock()
    tool_call.id = "call_1"
    tool_call.function.name = "echo"
    tool_call.function.arguments = json.dumps({"text": "loop"})

    loop = AgentLoop(
        client=MagicMock(),
        model="test-model",
        system_prompt="sys",
        registry=registry,
        store=store,
        duplicate_tool_call_limit=2,
    )
    loop.client.chat.completions.create.return_value = make_response(tool_calls=[tool_call])

    result = loop.run("repeat", stream=False)

    assert "repeatedly requested the same tool call" in result
    assert "after reflection" in result.lower()
    assert loop.client.chat.completions.create.call_count == 3
    assert loop.last_reflection_count == 1
    assert store.load_history()[-1]["content"] == result


def test_agent_loop_stops_when_changed_calls_return_same_observation(tmp_path):
    registry = ToolRegistry()

    @registry.tool(description="search with no results")
    def search(query: str) -> str:
        return "no matches"

    store = MemoryStore(path=tmp_path / "memory.json", max_history=10)

    def tool_call(call_id: str, query: str):
        call = MagicMock()
        call.id = call_id
        call.function.name = "search"
        call.function.arguments = json.dumps({"query": query})
        return call

    loop = AgentLoop(
        client=MagicMock(),
        model="test-model",
        system_prompt="sys",
        registry=registry,
        store=store,
        duplicate_tool_call_limit=3,
    )
    loop.client.chat.completions.create.side_effect = [
        make_response(tool_calls=[tool_call("call_1", "alpha")]),
        make_response(tool_calls=[tool_call("call_2", "beta")]),
        make_response(tool_calls=[tool_call("call_3", "gamma")]),
        make_response(content="I changed approach and will report the blocker."),
    ]

    result = loop.run("find it", stream=False)

    assert result == "I changed approach and will report the blocker."
    assert loop.client.chat.completions.create.call_count == 4
    reflection_request = loop.client.chat.completions.create.call_args_list[-1]
    reflection_messages = reflection_request.kwargs["messages"]
    reflection_prompt = next(
        message
        for message in reflection_messages
        if message.get("role") == "system"
        and "REFLECTION_REQUIRED" in str(message.get("content", ""))
    )
    assert reflection_prompt["role"] == "system"
    assert "REFLECTION_REQUIRED" in reflection_prompt["content"]
    assert "repeated_observation" in reflection_prompt["content"]
    assert loop.last_reflection_count == 1
    assert store.load_history()[-1]["content"] == result
    assert not any(
        message.get("role") == "system"
        and "REFLECTION_REQUIRED" in str(message.get("content", ""))
        for message in store.load_history()
    )


def test_agent_loop_stops_when_no_progress_repeats_after_reflection(tmp_path):
    registry = ToolRegistry()

    @registry.tool(description="search with no results")
    def search(query: str) -> str:
        return "no matches"

    store = MemoryStore(path=tmp_path / "memory.json", max_history=10)

    def tool_call(call_id: str, query: str):
        call = MagicMock()
        call.id = call_id
        call.function.name = "search"
        call.function.arguments = json.dumps({"query": query})
        return call

    loop = AgentLoop(
        client=MagicMock(),
        model="test-model",
        system_prompt="sys",
        registry=registry,
        store=store,
    )
    loop.client.chat.completions.create.side_effect = [
        make_response(tool_calls=[tool_call("call_1", "alpha")]),
        make_response(tool_calls=[tool_call("call_2", "beta")]),
        make_response(tool_calls=[tool_call("call_3", "gamma")]),
        make_response(tool_calls=[tool_call("call_4", "delta")]),
    ]

    result = loop.run("find it", stream=False)

    assert "no progress" in result.lower()
    assert "after reflection" in result.lower()
    assert loop.client.chat.completions.create.call_count == 4
    assert loop.last_reflection_count == 1
    assert store.load_history()[-1]["content"] == result


def test_reflection_does_not_reset_max_tool_iterations(tmp_path):
    registry = ToolRegistry()

    @registry.tool(description="search with no results")
    def search(query: str) -> str:
        return "no matches"

    store = MemoryStore(path=tmp_path / "memory.json", max_history=10)

    def tool_call(call_id: str, query: str):
        call = MagicMock()
        call.id = call_id
        call.function.name = "search"
        call.function.arguments = json.dumps({"query": query})
        return call

    loop = AgentLoop(
        client=MagicMock(),
        model="test-model",
        system_prompt="sys",
        registry=registry,
        store=store,
        max_tool_iterations=3,
    )
    loop.client.chat.completions.create.side_effect = [
        make_response(tool_calls=[tool_call("call_1", "alpha")]),
        make_response(tool_calls=[tool_call("call_2", "beta")]),
        make_response(tool_calls=[tool_call("call_3", "gamma")]),
    ]

    result = loop.run("find it", stream=False)

    assert "maximum tool iteration limit" in result
    assert loop.client.chat.completions.create.call_count == 3
    assert loop.last_reflection_count == 1


def test_tool_exception_is_returned_as_structured_error(tmp_path):
    registry = ToolRegistry()

    @registry.tool(description="explode")
    def explode(value: str) -> str:
        raise ValueError(f"bad value: {value}")

    store = MemoryStore(path=tmp_path / "memory.json", max_history=10)
    tool_call = MagicMock()
    tool_call.id = "call_1"
    tool_call.function.name = "explode"
    tool_call.function.arguments = json.dumps({"value": "x"})

    loop = AgentLoop(
        client=MagicMock(),
        model="test-model",
        system_prompt="sys",
        registry=registry,
        store=store,
    )
    loop.client.chat.completions.create.side_effect = [
        make_response(tool_calls=[tool_call]),
        make_response(content="handled"),
    ]

    result = loop.run("explode", stream=False)

    assert result == "handled"
    payload = json.loads(store.load_history()[2]["content"])
    assert payload["error_type"] == "ValueError"
    assert payload["message"] == "bad value: x"
    assert "recovery_hint" in payload


def test_retryable_tool_error_is_retried_before_returning_to_model(tmp_path):
    registry = ToolRegistry()
    calls = {"count": 0}

    @registry.tool(description="flaky")
    def flaky() -> str:
        calls["count"] += 1
        if calls["count"] == 1:
            raise TimeoutError("temporary timeout")
        return "ok after retry"

    store = MemoryStore(path=tmp_path / "memory.json", max_history=10)
    tool_call = MagicMock()
    tool_call.id = "call_1"
    tool_call.function.name = "flaky"
    tool_call.function.arguments = json.dumps({})

    loop = AgentLoop(
        client=MagicMock(),
        model="test-model",
        system_prompt="sys",
        registry=registry,
        store=store,
        max_tool_retries=1,
    )
    loop.client.chat.completions.create.side_effect = [
        make_response(tool_calls=[tool_call]),
        make_response(content="handled"),
    ]

    result = loop.run("run flaky", stream=False)

    assert result == "handled"
    assert calls["count"] == 2
    assert store.load_history()[2]["content"] == "ok after retry"


def test_retryable_tool_error_reports_retry_exhaustion(tmp_path):
    registry = ToolRegistry()

    @registry.tool(description="always timeout")
    def always_timeout() -> str:
        raise TimeoutError("still down")

    store = MemoryStore(path=tmp_path / "memory.json", max_history=10)
    tool_call = MagicMock()
    tool_call.id = "call_1"
    tool_call.function.name = "always_timeout"
    tool_call.function.arguments = json.dumps({})

    loop = AgentLoop(
        client=MagicMock(),
        model="test-model",
        system_prompt="sys",
        registry=registry,
        store=store,
        max_tool_retries=1,
    )
    loop.client.chat.completions.create.side_effect = [
        make_response(tool_calls=[tool_call]),
        make_response(content="handled"),
    ]

    result = loop.run("run failing tool", stream=False)

    assert result == "handled"
    payload = json.loads(store.load_history()[2]["content"])
    assert payload["error_type"] == "CommandTimeoutError"
    assert payload["retryable"] is True
    assert "Automatic retry exhausted" in payload["recovery_hint"]


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
    # This guards the user-visible streaming contract: chunks should be emitted
    # incrementally and still persist as one final assistant message.
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
            # The tool name and JSON args are intentionally split across chunks
            # to verify streamed tool-call reassembly.
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
        # Interrupt after a visible chunk to ensure partial output is returned
        # without committing an incomplete turn to persistent history.
        yield make_stream_chunk(content="Par")
        raise KeyboardInterrupt

    loop.client.chat.completions.create.return_value = interrupted_stream()

    chunks = []
    result = loop.run("Hi", stream=True, on_text_chunk=chunks.append)

    assert result == "Par"
    assert chunks == ["Par"]
    assert store.load_history() == []


def test_high_risk_tool_requires_approval(tmp_path):
    registry = ToolRegistry()

    @registry.tool(description="危险工具", risk_level="high")
    def wipe(target: str) -> str:
        return f"WIPED:{target}"

    store = MemoryStore(path=tmp_path / "memory.json", max_history=10)
    tool_call = MagicMock()
    tool_call.id = "call_1"
    tool_call.function.name = "wipe"
    tool_call.function.arguments = json.dumps({"target": "demo"})

    loop = AgentLoop(
        client=MagicMock(),
        model="test-model",
        system_prompt="sys",
        registry=registry,
        store=store,
        auto_approve=False,
        confirm_tool=lambda name, arguments, risk: False,
    )
    loop.client.chat.completions.create.side_effect = [
        make_response(tool_calls=[tool_call]),
        make_response(content="Denied handled."),
    ]

    result = loop.run("run wipe", stream=False)

    assert result == "Denied handled."
    history = store.load_history()
    assert history[2]["role"] == "tool"
    assert history[2]["content"] == "User denied tool execution."


def test_medium_risk_tool_requires_approval_by_default(tmp_path):
    registry = ToolRegistry()

    @registry.tool(description="中风险工具", risk_level="medium")
    def edit(target: str) -> str:
        return f"EDITED:{target}"

    store = MemoryStore(path=tmp_path / "memory.json", max_history=10)
    tool_call = MagicMock()
    tool_call.id = "call_1"
    tool_call.function.name = "edit"
    tool_call.function.arguments = json.dumps({"target": "demo"})

    loop = AgentLoop(
        client=MagicMock(),
        model="test-model",
        system_prompt="sys",
        registry=registry,
        store=store,
        safe_mode=False,
        confirm_tool=lambda name, arguments, risk: False,
    )
    loop.client.chat.completions.create.side_effect = [
        make_response(tool_calls=[tool_call]),
        make_response(content="Denied handled."),
    ]

    result = loop.run("edit", stream=False)

    assert result == "Denied handled."
    assert store.load_history()[2]["content"] == "User denied tool execution."


def test_medium_risk_tool_requires_approval_in_safe_mode(tmp_path):
    registry = ToolRegistry()

    @registry.tool(description="中风险工具", risk_level="medium")
    def edit(target: str) -> str:
        return f"EDITED:{target}"

    store = MemoryStore(path=tmp_path / "memory.json", max_history=10)
    tool_call = MagicMock()
    tool_call.id = "call_1"
    tool_call.function.name = "edit"
    tool_call.function.arguments = json.dumps({"target": "demo"})

    loop = AgentLoop(
        client=MagicMock(),
        model="test-model",
        system_prompt="sys",
        registry=registry,
        store=store,
        safe_mode=True,
        confirm_tool=lambda name, arguments, risk: False,
    )
    loop.client.chat.completions.create.side_effect = [
        make_response(tool_calls=[tool_call]),
        make_response(content="Denied handled."),
    ]

    result = loop.run("edit", stream=False)

    assert result == "Denied handled."
    assert store.load_history()[2]["content"] == "User denied tool execution."


def test_auto_approve_skips_high_risk_confirmation(tmp_path):
    registry = ToolRegistry()

    @registry.tool(description="危险工具", risk_level="high")
    def wipe(target: str) -> str:
        return f"WIPED:{target}"

    store = MemoryStore(path=tmp_path / "memory.json", max_history=10)
    tool_call = MagicMock()
    tool_call.id = "call_1"
    tool_call.function.name = "wipe"
    tool_call.function.arguments = json.dumps({"target": "demo"})

    confirm_calls = []
    loop = AgentLoop(
        client=MagicMock(),
        model="test-model",
        system_prompt="sys",
        registry=registry,
        store=store,
        auto_approve=True,
        confirm_tool=lambda name, arguments, risk: confirm_calls.append((name, risk)),
    )
    loop.client.chat.completions.create.side_effect = [
        make_response(tool_calls=[tool_call]),
        make_response(content="ok"),
    ]

    result = loop.run("wipe", stream=False)

    assert result == "ok"
    assert confirm_calls == []
    assert store.load_history()[2]["content"] == "WIPED:demo"


def test_model_config_is_forwarded_to_chat_completions(registry, store):
    loop = AgentLoop(
        client=MagicMock(),
        model="test-model",
        system_prompt="sys",
        registry=registry,
        store=store,
        temperature=0.2,
        max_tokens=256,
    )
    loop.client.chat.completions.create.return_value = make_response(content="ok")

    loop.run("hello", stream=False)

    kwargs = loop.client.chat.completions.create.call_args.kwargs
    assert kwargs["temperature"] == 0.2
    assert kwargs["max_tokens"] == 256


def test_provider_tool_choice_rejection_retries_without_tool_choice(registry, store):
    loop = AgentLoop(
        client=MagicMock(),
        model="test-model",
        system_prompt="sys",
        registry=registry,
        store=store,
    )
    loop.client.chat.completions.create.side_effect = [
        Exception(
            "Error code: 404 - No endpoints found that support the provided "
            "'tool_choice' value"
        ),
        make_response(content="ok"),
    ]

    result = loop.run("hello", stream=False)

    assert result == "ok"
    first_kwargs = loop.client.chat.completions.create.call_args_list[0].kwargs
    second_kwargs = loop.client.chat.completions.create.call_args_list[1].kwargs
    assert first_kwargs["tool_choice"] == "auto"
    assert "tool_choice" not in second_kwargs
    assert loop._tool_choice_disabled_by_provider is True


def test_tool_choice_can_be_disabled_by_config(registry, store):
    loop = AgentLoop(
        client=MagicMock(),
        model="test-model",
        system_prompt="sys",
        registry=registry,
        store=store,
        tool_choice=None,
    )
    loop.client.chat.completions.create.return_value = make_response(content="ok")

    loop.run("hello", stream=False)

    kwargs = loop.client.chat.completions.create.call_args.kwargs
    assert "tools" in kwargs
    assert "tool_choice" not in kwargs


def test_memory_injection_includes_semantic_and_episodic_context(registry, tmp_path):
    store = MemoryStore(
        path=tmp_path / "memory.json",
        max_history=10,
        retrieval_top_k=1,
        session_id="alpha",
    )
    store.remember_fact("Project language is Python.")
    store.append(
        [
            {"role": "user", "content": "What should we build first?"},
            {"role": "assistant", "content": "Build the Python CLI first."},
        ]
    )
    store.create_session(make_current=True)

    loop = AgentLoop(
        client=MagicMock(),
        model="test-model",
        system_prompt="sys",
        registry=registry,
        store=store,
        memory_injection_limit=1,
    )
    loop.client.chat.completions.create.return_value = make_response(content="ok")

    loop.run("Need the Python CLI plan", stream=False)

    messages = loop.client.chat.completions.create.call_args.kwargs["messages"]
    contents = [m["content"] for m in messages if m.get("content")]
    assert any("Semantic memory" in content for content in contents)
    assert any("Project language is Python." in content for content in contents)
    assert any("Episodic memory" in content for content in contents)
    assert any("Build the Python CLI first." in content for content in contents)


def test_agent_loop_injects_runtime_compression_summary_when_history_is_large(registry, tmp_path):
    store = MemoryStore(
        path=tmp_path / "memory.json",
        max_history=20,
        max_context_tokens=45,
        compression_keep_recent=1,
    )
    store.append(
        [
            {"role": "user", "content": "first " * 30},
            {"role": "assistant", "content": "second " * 30},
            {"role": "user", "content": "latest raw message"},
        ]
    )

    loop = AgentLoop(
        client=MagicMock(),
        model="test-model",
        system_prompt="sys",
        registry=registry,
        store=store,
    )
    loop.client.chat.completions.create.return_value = make_response(content="ok")

    loop.run("new message", stream=False)

    messages = loop.client.chat.completions.create.call_args.kwargs["messages"]
    contents = [message["content"] for message in messages if message.get("content")]
    assert any("Runtime compression summary" in content for content in contents)
    assert any("latest raw message" in content for content in contents)
    assert any(content == "new message" for content in contents)
