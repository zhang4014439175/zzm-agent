import json

from zzm_agent.core.agent_loop import AgentLoop
from zzm_agent.memory.store import MemoryStore

from zzm_agent.eval.replay import (
    MockToolRegistry,
    ReplayLLM,
    ReplayToolCall,
    ReplayTurn,
)


def replay_key(name, **arguments):
    return name, tuple(sorted(arguments.items()))


def make_loop(
    tmp_path,
    turns,
    registry,
    *,
    stream=False,
    max_tool_iterations=20,
    duplicate_tool_call_limit=3,
    confirm_tool=None,
):
    client = ReplayLLM(turns)
    loop = AgentLoop(
        client=client,
        model="replay-model",
        system_prompt="You are a deterministic replay agent.",
        registry=registry,
        store=MemoryStore(path=tmp_path / "memory.json", max_history=20),
        max_tool_iterations=max_tool_iterations,
        duplicate_tool_call_limit=duplicate_tool_call_limit,
        confirm_tool=confirm_tool,
    )
    return loop, client, stream


def test_normal_tool_flow(tmp_path):
    registry = MockToolRegistry(
        {
            replay_key("read_file", path="app.py"): "def main():\n    return 'ok'\n",
        }
    )
    loop, client, stream = make_loop(
        tmp_path,
        [
            ReplayTurn(tool_calls=[ReplayToolCall("read_file", {"path": "app.py"})]),
            ReplayTurn(content="The file defines main."),
        ],
        registry,
        stream=True,
    )

    chunks = []
    result = loop.run("Inspect app.py", stream=stream, on_text_chunk=chunks.append)

    assert result == "The file defines main."
    assert "".join(chunks) == result
    assert client.call_count == 2
    assert registry.calls == [("read_file", {"path": "app.py"})]
    assert loop.store.load_history()[2]["content"] == "def main():\n    return 'ok'\n"
    assert client.requests[0]["stream"] is True


def test_tool_error_recovery(tmp_path):
    registry = MockToolRegistry(
        {
            replay_key("read_file", path="missing.py"): FileNotFoundError("missing.py"),
            replay_key("grep_search", pattern="target"): "src/real.py:10: target",
        }
    )
    loop, client, stream = make_loop(
        tmp_path,
        [
            ReplayTurn(tool_calls=[ReplayToolCall("read_file", {"path": "missing.py"})]),
            ReplayTurn(tool_calls=[ReplayToolCall("grep_search", {"pattern": "target"})]),
            ReplayTurn(content="Found target in src/real.py."),
        ],
        registry,
    )

    result = loop.run("Find target", stream=stream)

    assert result == "Found target in src/real.py."
    assert client.call_count == 3
    assert registry.calls == [
        ("read_file", {"path": "missing.py"}),
        ("grep_search", {"pattern": "target"}),
    ]
    error_payload = json.loads(loop.store.load_history()[2]["content"])
    assert error_payload["error_type"] == "FileNotFoundError"
    assert loop.store.load_history()[4]["content"] == "src/real.py:10: target"


def test_duplicate_call_stop(tmp_path):
    registry = MockToolRegistry(
        {
            replay_key("grep_search", pattern="needle"): "no matches",
        }
    )
    loop, client, stream = make_loop(
        tmp_path,
        [
            ReplayTurn(tool_calls=[ReplayToolCall("grep_search", {"pattern": "needle"})]),
            ReplayTurn(tool_calls=[ReplayToolCall("grep_search", {"pattern": "needle"})]),
            ReplayTurn(tool_calls=[ReplayToolCall("grep_search", {"pattern": "needle"})]),
            ReplayTurn(tool_calls=[ReplayToolCall("grep_search", {"pattern": "needle"})]),
        ],
        registry,
        duplicate_tool_call_limit=3,
    )

    result = loop.run("Search repeatedly", stream=stream)

    assert "repeatedly requested the same tool call" in result
    assert "after reflection" in result.lower()
    assert client.call_count == 4
    assert loop.last_reflection_count == 1
    assert registry.calls == [
        ("grep_search", {"pattern": "needle"}),
        ("grep_search", {"pattern": "needle"}),
    ]
    assert loop.store.load_history()[-1]["content"] == result


def test_iteration_limit(tmp_path):
    registry = MockToolRegistry(
        {
            replay_key("read_file", path="a.py"): "same result",
            replay_key("read_file", path="b.py"): "same result",
        }
    )
    loop, client, stream = make_loop(
        tmp_path,
        [
            ReplayTurn(tool_calls=[ReplayToolCall("read_file", {"path": "a.py"})]),
            ReplayTurn(tool_calls=[ReplayToolCall("read_file", {"path": "b.py"})]),
            ReplayTurn(tool_calls=[ReplayToolCall("read_file", {"path": "c.py"})]),
        ],
        registry,
        max_tool_iterations=2,
    )

    result = loop.run("Keep reading", stream=stream)

    assert "maximum tool iteration limit" in result
    assert client.call_count == 2
    assert registry.calls == [
        ("read_file", {"path": "a.py"}),
        ("read_file", {"path": "b.py"}),
    ]


def test_user_deny_high_risk(tmp_path):
    registry = MockToolRegistry(
        {},
        risk_levels={"run_shell": "high"},
    )
    loop, client, stream = make_loop(
        tmp_path,
        [
            ReplayTurn(tool_calls=[ReplayToolCall("run_shell", {"command": "rm -rf /"})]),
            ReplayTurn(content="I will avoid that command."),
        ],
        registry,
        confirm_tool=lambda name, arguments, risk: False,
    )

    result = loop.run("Remove everything", stream=stream)

    assert result == "I will avoid that command."
    assert client.call_count == 2
    assert registry.calls == []
    assert loop.store.load_history()[2]["content"] == "User denied tool execution."
