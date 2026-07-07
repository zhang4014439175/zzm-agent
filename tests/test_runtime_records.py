import json

from zzm_agent.core.runtime_records import (
    ArtifactStore,
    CheckpointStore,
    EventBus,
    EventJsonlStore,
)
from zzm_agent.core.runtime_state import ApplicationState, ConversationState, TurnState


def test_event_bus_records_events_and_isolates_observer_errors():
    bus = EventBus()
    observed = []

    bus.subscribe(lambda event: observed.append(event.event_type))
    bus.subscribe(lambda _event: (_ for _ in ()).throw(RuntimeError("ui failed")))

    event = bus.publish(
        "loop.transition",
        {"from": "calling_model", "to": "executing_tools"},
        session_id="session-a",
        turn_id="turn-1",
    )

    assert event.sequence == 1
    assert observed == ["loop.transition"]
    assert bus.events == [event]
    assert bus.observer_errors[0]["error"] == "ui failed"
    assert bus.to_records()[0]["payload"]["to"] == "executing_tools"


def test_event_jsonl_store_round_trips_runtime_events(tmp_path):
    bus = EventBus()
    store = EventJsonlStore(tmp_path / "events.jsonl")
    event = bus.publish("usage.recorded", {"total_tokens": 12})

    store.append(event)
    restored = store.read()

    assert restored[0].event_type == "usage.recorded"
    assert restored[0].payload["total_tokens"] == 12
    assert json.loads((tmp_path / "events.jsonl").read_text(encoding="utf-8").splitlines()[0])[
        "event_id"
    ] == event.event_id


def test_artifact_store_saves_large_results_to_files(tmp_path):
    store = ArtifactStore(tmp_path)

    record = store.save_text(
        "full output\n" * 20,
        kind="tool_result",
        summary="long shell output",
        session_id="session-a",
        turn_id="turn-1",
        metadata={"tool_call_id": "call-1"},
    )

    assert record.artifact_id.startswith("artifact-")
    assert record.size_bytes > 100
    assert record.checksum.startswith("sha256:")
    assert store.read_text(record.artifact_id).startswith("full output")
    assert store.list(session_id="session-a") == [record]
    assert store.to_records()[0]["metadata"]["tool_call_id"] == "call-1"


def test_checkpoint_store_saves_latest_recoverable_state(tmp_path):
    store = CheckpointStore(tmp_path)
    turn = TurnState(user_input="hello", turn_id="turn-1")
    turn.start()

    first = store.save(
        scope="turn",
        state={"turn_id": turn.turn_id, "status": str(turn.status.value)},
        session_id="session-a",
        turn_id=turn.turn_id,
        label="turn-started",
    )
    second = store.save(
        scope="turn",
        state={"turn_id": turn.turn_id, "status": "completed"},
        session_id="session-a",
        turn_id=turn.turn_id,
        label="turn-completed",
    )
    restored = CheckpointStore(tmp_path)
    restored.load_from_disk()

    assert first.checksum.startswith("sha256:")
    assert store.latest(scope="turn", session_id="session-a") == second
    assert restored.get(first.checkpoint_id).state["status"] == "in_progress"
    assert restored.latest(scope="turn", turn_id="turn-1").label == "turn-completed"


def test_runtime_states_own_event_artifact_and_checkpoint_stores():
    app = ApplicationState()
    conversation = ConversationState(session_id="session-a")

    app_event = app.events.publish("session.created", {"session_id": "session-a"})
    artifact = conversation.artifacts.save_json(
        {"result": "ok"},
        kind="report",
        session_id="session-a",
    )
    checkpoint = conversation.checkpoints.save(
        scope="conversation",
        state={"session_id": conversation.session_id},
        session_id="session-a",
    )

    assert app.events.events == [app_event]
    assert artifact.mime_type == "application/json"
    assert conversation.artifacts.read_text(artifact.artifact_id)
    assert checkpoint.state["session_id"] == "session-a"
