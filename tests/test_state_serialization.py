import json

import pytest

from zzm_agent.core.observability import TokenUsage
from zzm_agent.core.runtime_state import (
    ConversationState,
    LoopPhase,
    MemoryLoadState,
    PermissionScope,
    TurnState,
)
from zzm_agent.core.state_serialization import (
    RecoveryStatus,
    RecoveryValidationContext,
    RecoveryValidator,
    STATE_SCHEMA_VERSION,
    StateEnvelope,
    StateSerializationError,
    StateSnapshotStore,
    make_state_envelope,
    migrate_state_record,
)
from zzm_agent.memory.io import StorageCorruptionError


def test_state_envelope_records_schema_version_and_checksum():
    turn = TurnState(user_input="hello", turn_id="turn-1")
    turn.complete("hi", usage=TokenUsage(total_tokens=3))

    envelope = make_state_envelope(turn, state_type="turn")
    record = envelope.to_record()
    restored = StateEnvelope.from_record(record)

    assert record["schema_version"] == STATE_SCHEMA_VERSION
    assert record["state_type"] == "turn"
    assert record["checksum"].startswith("sha256:")
    assert restored.payload["turn_id"] == "turn-1"


def test_state_envelope_rejects_tampered_payload():
    record = make_state_envelope(
        {"answer": "original"},
        state_type="demo",
    ).to_record()
    record["payload"]["answer"] = "changed"

    with pytest.raises(StateSerializationError, match="checksum mismatch"):
        StateEnvelope.from_record(record)


def test_migrate_legacy_state_record_wraps_payload():
    legacy = {
        "state_type": "turn",
        "state": {"turn_id": "turn-1", "status": "completed"},
    }

    migrated = migrate_state_record(legacy)

    assert migrated["schema_version"] == STATE_SCHEMA_VERSION
    assert migrated["payload"]["turn_id"] == "turn-1"
    assert migrated["metadata"]["migrated_from"] == "legacy"


def test_snapshot_store_round_trips_conversation_state(tmp_path):
    conversation = ConversationState(session_id="session-a")
    conversation.skills.add("python")
    conversation.file_reads.record_read(
        normalized_path=str(tmp_path / "app.py"),
        content="print('hi')\n",
        size_bytes=12,
        mtime_ns=10,
        start_line=1,
        end_line=1,
    )
    conversation.memories.record_file_source(
        path=str(tmp_path / "MEMORY.md"),
        source_type="project_memory",
        version="10:20",
    )
    request = conversation.permissions.request_permission(
        tool_name="shell",
        arguments={"cmd": "pytest"},
        risk_level="high",
        scope=PermissionScope.ONCE,
    )
    conversation.permissions.deny_request(request.request_id, reason="not now")
    turn = conversation.start_turn("run tests", turn_id="turn-1")
    loop = turn.start_loop()
    loop.record_model_call()
    conversation.events.publish("turn.started", {"turn_id": turn.turn_id})
    artifact = conversation.artifacts.save_text(
        "full output",
        kind="tool-log",
        summary="pytest output",
        turn_id=turn.turn_id,
    )
    turn.artifacts.append(artifact.to_record())

    store = StateSnapshotStore(tmp_path / "conversation.json")
    store.save(conversation, state_type="conversation")
    restored = store.load_state(ConversationState.from_record)

    assert restored is not None
    assert restored.session_id == "session-a"
    assert restored.active_turn is not None
    assert restored.active_turn.turn_id == "turn-1"
    assert restored.active_turn.loop is not None
    assert restored.active_turn.loop.phase is LoopPhase.CALLING_MODEL
    assert restored.permissions.denials[0].reason == "not now"
    assert restored.memories.memory_file_versions[str(tmp_path / "MEMORY.md")] == "10:20"
    assert restored.events.to_records()[0]["event_type"] == "turn.started"
    assert restored.artifacts.to_records()[0]["summary"] == "pytest output"


def test_snapshot_store_uses_storage_io_corrupt_file_quarantine(tmp_path):
    path = tmp_path / "turn.json"
    store = StateSnapshotStore(path)
    store.save({"turn_id": "turn-1"}, state_type="turn")
    path.write_text("{bad json", encoding="utf-8")

    with pytest.raises(StorageCorruptionError):
        store.load_envelope()

    assert path.exists()
    assert json.loads(path.read_text(encoding="utf-8"))["state_type"] == "turn"
    assert list(tmp_path.glob("turn.json.corrupt.*"))


def test_recovery_validator_accepts_completed_turn_with_valid_context(tmp_path):
    artifact_path = tmp_path / "artifact.txt"
    artifact_path.write_text("log", encoding="utf-8")
    envelope = make_state_envelope(
        {"turn_id": "turn-1", "status": "completed"},
        state_type="turn",
    )

    decision = RecoveryValidator().validate(
        envelope,
        context=RecoveryValidationContext(
            workspace_path=tmp_path,
            artifact_paths=[artifact_path],
        ),
    )

    assert decision.status is RecoveryStatus.RECOVERABLE


def test_recovery_validator_blocks_running_turn_without_checkpoint():
    turn = TurnState(user_input="run", turn_id="turn-1")
    turn.start()
    loop = turn.start_loop()
    loop.record_model_call()
    envelope = make_state_envelope(turn, state_type="turn")

    decision = RecoveryValidator().validate(envelope)

    assert decision.status is RecoveryStatus.BLOCKED
    assert decision.reason == "running_state_requires_checkpoint"
    assert "loop_phase=calling_model" in decision.details


def test_recovery_validator_blocks_missing_artifact(tmp_path):
    envelope = make_state_envelope(
        {"turn_id": "turn-1", "status": "completed"},
        state_type="turn",
    )

    decision = RecoveryValidator().validate(
        envelope,
        context=RecoveryValidationContext(
            workspace_path=tmp_path,
            artifact_paths=[tmp_path / "missing.txt"],
        ),
    )

    assert decision.status is RecoveryStatus.BLOCKED
    assert decision.reason == "artifact_missing"


def test_recovery_validator_blocks_changed_memory_file_versions():
    state = MemoryLoadState()
    state.record_file_source(
        path="/workspace/MEMORY.md",
        source_type="project_memory",
        version="1:20",
    )
    envelope = make_state_envelope(
        {
            "turn_id": "turn-1",
            "status": "completed",
            "memories": state.to_record(),
        },
        state_type="turn",
    )

    decision = RecoveryValidator().validate(
        envelope,
        context=RecoveryValidationContext(
            memory_file_versions=state.memory_file_versions,
            current_file_versions={"/workspace/MEMORY.md": "2:20"},
        ),
    )

    assert decision.status is RecoveryStatus.BLOCKED
    assert decision.reason == "file_version_changed"
