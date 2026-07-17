from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from zzm_agent.core.model_stream import ModelStreamEvent
from zzm_agent.core.observability import TokenUsage, UsageState
from zzm_agent.core.query_engine import QueryEngine
from zzm_agent.core.runtime_records import ArtifactStore
from zzm_agent.core.segments import SegmentResult
from zzm_agent.core.state import PermissionState, TurnState, TurnStatus
from zzm_agent.runtime.events import RuntimeEvent
from zzm_agent.runtime.journal import ExecutionJournal


def test_execution_journal_assigns_global_sequence_and_filters_state_links(tmp_path):
    """验证不同来源事件共享连续序号，并可按会话、Turn 和游标查询。"""
    journal = ExecutionJournal(tmp_path / "execution.jsonl")
    first = RuntimeEvent("turn.started", session_id="s1", turn_id="t1", source="cli")
    second = RuntimeEvent("tool.completed", session_id="s1", turn_id="t1", source="runtime")
    third = RuntimeEvent("turn.started", session_id="s2", turn_id="t2", source="replay")

    journal.append(first)
    journal.append(second)
    journal.append(third)

    assert [event.sequence for event in journal.events] == [1, 2, 3]
    assert journal.query(session_id="s1", after_sequence=1) == [second]
    assert list(journal.replay(after_sequence=1)) == [second, third]


def test_execution_journal_restores_sequence_and_rejects_future_schema(tmp_path):
    """验证重启后序号不会回退，未来未知事件版本会明确拒绝。"""
    path = tmp_path / "execution.jsonl"
    journal = ExecutionJournal(path)
    journal.append(RuntimeEvent("one"))

    restored = ExecutionJournal(path)
    appended = restored.append(RuntimeEvent("two"))

    assert appended.sequence == 2
    assert json.loads(path.read_text(encoding="utf-8").splitlines()[0])["schema_version"] == 1
    with pytest.raises(ValueError, match="Unsupported runtime event schema"):
        restored.append(RuntimeEvent("future", schema_version=99))


def test_query_engine_exposes_same_runtime_facts_used_by_journal_and_clients(tmp_path):
    """验证 QueryEngine 把模型流转换为带状态关联的统一事实并写入同一 Journal。"""
    turn = TurnState(user_input="hello", turn_id="turn-fixed")
    turn.start_loop()
    turn.complete("done")

    class Loop:
        store = SimpleNamespace(session_id="session-fixed")
        artifact_store = ArtifactStore(tmp_path / "artifacts")
        last_turn_state = turn
        cumulative_usage = TokenUsage()
        usage_state = UsageState()
        permission_state = PermissionState()

        def run_segment(self, user_input, **kwargs):
            """发出一个兼容流事件并返回完成执行段。"""
            kwargs["on_stream_event"](ModelStreamEvent.content_delta("done"))
            return SegmentResult(
                status=TurnStatus.COMPLETED,
                reason="model_completed",
                reply="done",
                turn=turn,
            )

    journal = ExecutionJournal(tmp_path / "query.jsonl")
    engine = QueryEngine(agent_loop=Loop(), execution_journal=journal)

    result = engine.submit_message("hello")

    assert result.runtime_events == journal.events
    assert [event.sequence for event in result.runtime_events] == list(
        range(1, len(result.runtime_events) + 1)
    )
    assert all(event.schema_version == 1 for event in result.runtime_events)
    assert all(event.session_id == "session-fixed" for event in result.runtime_events)
    assert any(event.event_type == "stream.content_delta" for event in result.runtime_events)
