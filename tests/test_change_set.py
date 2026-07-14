from zzm_agent.cli_support.commands import handle_slash
from zzm_agent.core.change_set import ChangeSetStore
from zzm_agent.core.observability import tool_end_event, tool_start_event


class DummyConsole:
    def __init__(self) -> None:
        self.lines: list[str] = []

    def print(self, value="", *args, **kwargs) -> None:
        self.lines.append(str(value))


def _start(call_id: str, path: str):
    return tool_start_event(
        tool_name="file_edit",
        tool_call_id=call_id,
        arguments={"path": path, "target": "old", "replacement": "new"},
        risk_level="medium",
    )


def _end(call_id: str, path: str):
    return tool_end_event(
        tool_name="file_edit",
        tool_call_id=call_id,
        arguments={"path": path, "target": "old", "replacement": "new"},
        risk_level="medium",
        status="success",
        duration_ms=1,
        result="Success",
        attempts=1,
    )


def test_changeset_records_patch_hashes_turn_and_persists(tmp_path):
    target = tmp_path / "demo.txt"
    target.write_text("old\n", encoding="utf-8")
    store = ChangeSetStore(tmp_path, session_id="session-a")

    store.capture_start(_start("call-1", "demo.txt"))
    target.write_text("new\n", encoding="utf-8")
    change = store.capture_end(_end("call-1", "demo.txt"), turn_id="turn-a")

    assert change is not None
    assert change.session_id == "session-a"
    assert change.turn_id == "turn-a"
    assert change.before_hash != change.after_hash
    assert "-old" in change.patch
    assert "+new" in change.patch
    restored_store = ChangeSetStore(tmp_path, session_id="session-a")
    assert restored_store.list_changesets()[0].change_set_id == change.change_set_id


def test_undo_restores_existing_file_and_removes_created_file(tmp_path):
    target = tmp_path / "demo.txt"
    target.write_text("old", encoding="utf-8")
    store = ChangeSetStore(tmp_path)
    store.capture_start(_start("call-1", "demo.txt"))
    target.write_text("new", encoding="utf-8")
    change = store.capture_end(_end("call-1", "demo.txt"))

    result = store.undo(change.change_set_id)

    assert result.undone is True
    assert target.read_text(encoding="utf-8") == "old"
    assert store.list_changesets()[0].status == "reverted"

    store.capture_start(_start("call-2", "created.txt"))
    created = tmp_path / "created.txt"
    created.write_text("created", encoding="utf-8")
    created_change = store.capture_end(_end("call-2", "created.txt"))
    assert store.undo(created_change.change_set_id).undone is True
    assert not created.exists()


def test_undo_refuses_to_overwrite_external_file_change(tmp_path):
    target = tmp_path / "demo.txt"
    target.write_text("old", encoding="utf-8")
    store = ChangeSetStore(tmp_path)
    store.capture_start(_start("call-1", "demo.txt"))
    target.write_text("agent value", encoding="utf-8")
    change = store.capture_end(_end("call-1", "demo.txt"))
    target.write_text("user value", encoding="utf-8")

    result = store.undo(change.change_set_id)

    assert result.undone is False
    assert "conflict" in result.message.lower()
    assert target.read_text(encoding="utf-8") == "user value"
    assert store.list_changesets()[0].status == "conflicted"


def test_slash_undo_reports_success_and_conflict(tmp_path):
    target = tmp_path / "demo.txt"
    target.write_text("old", encoding="utf-8")
    changes = ChangeSetStore(tmp_path)
    changes.capture_start(_start("call-1", "demo.txt"))
    target.write_text("new", encoding="utf-8")
    changes.capture_end(_end("call-1", "demo.txt"))
    console = DummyConsole()

    assert handle_slash("/undo", None, None, None, console, {"change_sets": changes})
    assert target.read_text(encoding="utf-8") == "old"
    assert any("Undid" in line for line in console.lines)
