from types import SimpleNamespace
from unittest.mock import MagicMock

from zzm_agent.core.agent_loop import AgentLoop
from zzm_agent.core.model_stream import ModelStreamEventKind
from zzm_agent.core.query_engine import QueryEngine
from zzm_agent.core.runtime_state import ConversationState
from zzm_agent.core.state_serialization import (
    RecoveryStatus,
    RecoveryValidationContext,
    RecoveryValidator,
    StateSnapshotStore,
)
from zzm_agent.core.tool_registry import ToolRegistry
from zzm_agent.memory.store import MemoryStore


def make_response(content=None, tool_calls=None):
    message = SimpleNamespace(content=content, tool_calls=tool_calls or [])
    choice = SimpleNamespace(message=message)
    return SimpleNamespace(choices=[choice], usage=None, error=None)


def make_stream_chunk(content=None, reasoning=None, usage=None):
    delta = SimpleNamespace(
        content=content,
        reasoning_summary=reasoning,
        tool_calls=[],
    )
    choice = SimpleNamespace(delta=delta)
    return SimpleNamespace(choices=[choice], usage=usage)


def make_agent_loop(tmp_path, *, session_id="p1") -> AgentLoop:
    registry = ToolRegistry()
    store = MemoryStore(
        path=tmp_path / f"{session_id}.json",
        max_history=10,
        session_id=session_id,
    )
    return AgentLoop(
        client=MagicMock(),
        model="test-model",
        system_prompt="sys",
        registry=registry,
        store=store,
    )


def test_p1_query_engine_snapshot_is_observable_and_recoverable(tmp_path):
    loop = make_agent_loop(tmp_path, session_id="p1-recoverable")
    loop.client.chat.completions.create.return_value = make_response(content="Hello!")
    snapshot_store = StateSnapshotStore(tmp_path / "conversation.json")
    engine = QueryEngine(agent_loop=loop, snapshot_store=snapshot_store)

    result = engine.submit_message("Hi", stream=False)
    envelope = snapshot_store.load_envelope()
    restored = snapshot_store.load_state(ConversationState.from_record)
    decision = RecoveryValidator().validate(
        envelope,
        context=RecoveryValidationContext(workspace_path=tmp_path),
    )

    assert result.reply == "Hello!"
    assert [event.kind for event in result.events] == [
        ModelStreamEventKind.STATUS,
        ModelStreamEventKind.FINAL_MESSAGE,
    ]
    assert envelope is not None
    assert envelope.metadata["reason"] == "turn.completed"
    assert decision.status is RecoveryStatus.RECOVERABLE
    assert restored is not None
    assert restored.session_id == "p1-recoverable"
    assert restored.active_turn is not None
    assert restored.active_turn.final_response == "Hello!"
    assert loop.store.load_history()[-1]["content"] == "Hello!"


def test_p1_stream_events_separate_reasoning_content_and_final(tmp_path):
    loop = make_agent_loop(tmp_path, session_id="p1-stream")
    loop.client.chat.completions.create.return_value = iter(
        [
            make_stream_chunk(reasoning="Need to inspect the request."),
            make_stream_chunk(content="Hel"),
            make_stream_chunk(content="lo"),
        ]
    )
    engine = QueryEngine(agent_loop=loop)

    result = engine.submit_message("Hi", stream=True)

    assert result.reply == "Hello"
    assert [
        event.kind
        for event in result.events
        if event.kind
        in {
            ModelStreamEventKind.REASONING_SUMMARY,
            ModelStreamEventKind.CONTENT_DELTA,
            ModelStreamEventKind.FINAL_MESSAGE,
        }
    ] == [
        ModelStreamEventKind.REASONING_SUMMARY,
        ModelStreamEventKind.CONTENT_DELTA,
        ModelStreamEventKind.CONTENT_DELTA,
        ModelStreamEventKind.FINAL_MESSAGE,
    ]
    assert [
        event.text
        for event in result.events
        if event.kind is ModelStreamEventKind.CONTENT_DELTA
    ] == ["Hel", "lo"]
    assert result.events[-1].text == "Hello"


def test_p1_legacy_agent_loop_run_remains_compatible(tmp_path):
    loop = make_agent_loop(tmp_path, session_id="p1-legacy")
    loop.client.chat.completions.create.return_value = make_response(content="Legacy OK")

    reply = loop.run("Hi", stream=False)

    assert reply == "Legacy OK"
    assert loop.store.load_history()[-1]["content"] == "Legacy OK"
    assert loop.last_turn_state is not None
    assert loop.last_turn_state.final_response == "Legacy OK"
