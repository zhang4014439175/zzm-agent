import json
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from zzm_agent.core.agent_loop import AgentLoop
from zzm_agent.core.observability import TokenUsage, UsageScope, UsageState
from zzm_agent.core.progress_monitor import ProgressSignal, ToolObservation
from zzm_agent.core.runtime_state import (
    ApplicationState,
    CancellationController,
    CancellationError,
    ConversationState,
    FileStateCache,
    LoopPhase,
    LoopState,
    LoopTransition,
    LoopTransitionError,
    MemoryLoadState,
    PermissionScope,
    PermissionState,
    PermissionStatus,
    TurnState,
    TurnStatus,
)
from zzm_agent.core.state_lifecycle import (
    PersistenceBoundary,
    RecoveryStrategy,
    StateScope,
)
from zzm_agent.core.tool_registry import ToolRegistry


class FakeStore:
    def __init__(self):
        self.history: list[dict] = []
        self.latest_context: dict | None = None

    def load_history(self) -> list[dict]:
        return list(self.history)

    def build_turn_messages(
        self,
        *,
        system_prompt: str,
        user_input: str,
        memory_limit: int,
    ) -> tuple[list[dict], dict]:
        return [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_input},
        ], {"total_tokens": 10, "max_context_tokens": 100}

    def append(self, messages: list[dict]) -> None:
        self.history.extend(messages)

    def save_latest_context(self, payload: dict) -> None:
        self.latest_context = payload


def make_response(content=None, tool_calls=None):
    msg = MagicMock()
    msg.content = content
    msg.tool_calls = tool_calls
    msg.role = "assistant"
    return SimpleNamespace(choices=[SimpleNamespace(message=msg)], usage=None, error=None)


def make_tool_call(name: str, arguments: dict):
    tool_call = MagicMock()
    tool_call.id = "call_1"
    tool_call.function.name = name
    tool_call.function.arguments = json.dumps(arguments)
    return tool_call


def test_application_state_owns_conversations_and_active_session():
    app = ApplicationState(configuration={"model": "demo"})

    conversation = app.get_or_create_conversation("session-a")

    assert ApplicationState.scope is StateScope.APPLICATION
    assert ApplicationState.policy().persistence is PersistenceBoundary.MEMORY_ONLY
    assert app.active_session_id == "session-a"
    assert app.active_conversation() is conversation
    assert app.get_or_create_conversation("session-a") is conversation


def test_conversation_state_starts_and_finishes_turn():
    conversation = ConversationState(session_id="session-a")
    usage = TokenUsage(prompt_tokens=10, completion_tokens=5, total_tokens=15, source="api")

    turn = conversation.start_turn("hello", turn_id="turn-1")
    loop = turn.start_loop()
    finished = conversation.finish_turn(
        "hi",
        messages=[
            {"role": "user", "content": "hello"},
            {"role": "assistant", "content": "hi"},
        ],
        usage=usage,
    )

    assert finished is turn
    assert turn.status is TurnStatus.COMPLETED
    assert turn.final_response == "hi"
    assert loop.phase is LoopPhase.COMPLETED
    assert conversation.active_turn is None
    assert conversation.usage.total_tokens == 15
    assert conversation.usage_state.conversation.total_tokens == 15
    assert len(conversation.messages) == 2


def test_cancellation_controller_propagates_callbacks_and_restores():
    controller = CancellationController(session_id="session-a")
    turn_token = controller.start_turn("turn-1")
    task_token = controller.start_task("task-1")
    child_token = controller.create_child("tool-1")
    callbacks: list[tuple[str, str | None]] = []

    child_token.register_callback(
        lambda token: callbacks.append((token.token_id, token.reason))
    )
    turn_token.cancel("user_interrupt")
    restored = CancellationController.from_record(controller.to_record())

    assert task_token.is_cancelled is True
    assert child_token.is_cancelled is True
    assert child_token.reason == "user_interrupt"
    assert child_token.cancelled_at == turn_token.cancelled_at
    assert callbacks == [(child_token.token_id, "user_interrupt")]
    with pytest.raises(CancellationError, match="user_interrupt"):
        child_token.raise_if_cancelled()
    restored_child = (
        restored.session_token.children[turn_token.token_id]
        .children[task_token.token_id]
        .children[child_token.token_id]
    )
    assert restored_child.reason == "user_interrupt"


def test_conversation_state_creates_turn_cancellation_token():
    conversation = ConversationState(session_id="session-a")

    turn = conversation.start_turn("hello", turn_id="turn-1")

    assert conversation.cancellation is not None
    assert turn.cancellation_token is conversation.cancellation.active_turn_token
    assert turn.cancellation_token is not None
    assert turn.cancellation_token.scope == "turn"


def test_usage_state_accumulates_model_turn_conversation_task_and_application():
    usage_state = UsageState(conversation_id="session-a", task_id="task-1")
    usage_state.start_turn("turn-1")
    call_usage = TokenUsage(
        prompt_tokens=20,
        completion_tokens=7,
        total_tokens=27,
        cache_creation_tokens=3,
        cache_read_tokens=4,
        reasoning_tokens=2,
        source="api",
    )

    accounted = usage_state.record_model_call(
        call_usage,
        model="demo-model",
        tool_schema_tokens=5,
    )
    usage_state.record_tool_calls(2)
    restored = UsageState.from_record(usage_state.to_record())

    assert accounted.model_calls == 1
    assert accounted.tool_schema_tokens == 5
    assert usage_state.snapshot(UsageScope.TURN).total_tokens == 27
    assert usage_state.snapshot(UsageScope.CONVERSATION).model_calls == 1
    assert usage_state.snapshot(UsageScope.TASK).tool_calls == 2
    assert usage_state.snapshot(UsageScope.APPLICATION).cache_read_tokens == 4
    assert usage_state.snapshot_for_model("demo-model").reasoning_tokens == 2
    assert restored.conversation_id == "session-a"
    assert restored.task_id == "task-1"
    assert restored.snapshot_for_model("demo-model").tool_schema_tokens == 5


def test_conversation_state_rejects_overlapping_active_turns():
    conversation = ConversationState(session_id="session-a")
    conversation.start_turn("first")

    with pytest.raises(RuntimeError, match="another turn is active"):
        conversation.start_turn("second")


def test_turn_state_tracks_skills_memory_permissions_artifacts_and_failure():
    turn = TurnState(user_input="inspect")
    turn.start()
    turn.discovered_skills.add("python")
    turn.loaded_memory_paths.add("memory/project.md")
    turn.permission_requests.append({"tool": "shell"})
    turn.permission_denials.append({"tool": "shell", "reason": "user"})
    turn.artifacts.append({"path": "report.md"})

    turn.fail("boom")

    assert TurnState.scope is StateScope.TURN
    assert TurnState.policy().recovery is RecoveryStrategy.ROLLBACK_PENDING
    assert turn.status is TurnStatus.FAILED
    assert turn.error == "boom"
    assert turn.discovered_skills == {"python"}
    assert turn.loaded_memory_paths == {"memory/project.md"}
    assert turn.permission_requests[0]["tool"] == "shell"
    assert turn.permission_denials[0]["reason"] == "user"
    assert turn.artifacts[0]["path"] == "report.md"


def test_permission_state_tracks_request_decision_grants_and_restore():
    permissions = PermissionState()
    request = permissions.request_permission(
        tool_name="run_shell",
        arguments={"command": "pytest"},
        risk_level="high",
        tool_call_id="call_1",
        turn_id="turn-1",
    )

    decision = permissions.approve_request(
        request.request_id,
        scope=PermissionScope.SESSION,
        reason="trusted for this session",
    )
    restored = PermissionState.from_record(permissions.to_record())

    assert request.request_id not in permissions.pending_requests
    assert decision.status is PermissionStatus.APPROVED_SESSION
    assert restored.decisions[0].tool_name == "run_shell"
    assert restored.find_active_grant(
        tool_name="run_shell",
        arguments={"command": "pytest"},
    ) is not None


def test_permission_state_denials_do_not_become_grants():
    permissions = PermissionState()
    request = permissions.request_permission(
        tool_name="write_file",
        arguments={"path": "x.txt", "content": "hello"},
        risk_level="high",
    )

    decision = permissions.deny_request(request.request_id, reason="user declined")

    assert decision.status is PermissionStatus.DENIED
    assert permissions.denials == [decision]
    assert permissions.find_active_grant(
        tool_name="write_file",
        arguments={"path": "x.txt", "content": "hello"},
    ) is None


def test_permission_state_orphans_pending_requests_once():
    permissions = PermissionState()
    request = permissions.request_permission(
        tool_name="run_shell",
        arguments={"command": "sleep 10"},
        risk_level="high",
    )

    orphaned = permissions.handle_orphaned_permissions()

    assert orphaned == [request]
    assert permissions.pending_requests == {}
    assert permissions.orphaned_requests[0].status is PermissionStatus.ORPHANED
    assert permissions.has_handled_orphaned_permission is True


def test_file_state_cache_tracks_ranges_versions_and_restore():
    cache = FileStateCache()
    state = cache.record_read(
        normalized_path="/workspace/app.py",
        content="a\nb\nc\n",
        size_bytes=6,
        mtime_ns=10,
        start_line=1,
        end_line=2,
    )
    same = cache.get_valid(
        normalized_path="/workspace/app.py",
        size_bytes=6,
        mtime_ns=10,
    )
    changed = cache.get_valid(
        normalized_path="/workspace/app.py",
        size_bytes=7,
        mtime_ns=11,
    )
    restored = FileStateCache.from_record(cache.to_record())

    assert same is state
    assert state.has_range(1, 2)
    assert changed is None
    assert "/workspace/app.py" in cache.invalidated_paths
    assert restored.invalidated_paths == {"/workspace/app.py"}


def test_file_state_cache_updates_after_agent_write():
    cache = FileStateCache()

    state = cache.update_after_write(
        normalized_path="/workspace/app.py",
        content="new\ncontent\n",
        size_bytes=12,
        mtime_ns=20,
    )

    assert state.agent_last_modified_at is not None
    assert state.line_count == 2
    assert state.version == 1
    assert cache.files["/workspace/app.py"].content == "new\ncontent\n"


def test_memory_load_state_tracks_sources_and_duplicates():
    state = MemoryLoadState()
    semantic = {
        "fact": "Project language is Python.",
        "normalized_fact": "project language is python.",
        "updated_at": "2026-01-01T00:00:00Z",
    }
    episodic = {
        "session_id": "alpha",
        "summary": "Build the CLI first.",
        "updated_at": "2026-01-02T00:00:00Z",
    }

    assert state.record_file_source(
        path="/workspace/MEMORY.md",
        source_type="project_memory",
        version="1:20",
    )
    assert not state.record_file_source(
        path="/workspace/MEMORY.md",
        source_type="project_memory",
        version="1:20",
    )
    assert state.record_semantic_memory(semantic)
    assert not state.record_semantic_memory(semantic)
    assert state.record_episodic_memory(episodic)
    restored = MemoryLoadState.from_record(state.to_record())

    assert restored.loaded_project_memory_paths["/workspace/MEMORY.md"] == "1:20"
    assert len(restored.injected_semantic_memory_ids) == 1
    assert len(restored.injected_episodic_memory_ids) == 1
    assert len(restored.duplicate_sources) == 2


def test_loop_state_tracks_iterations_observations_and_reflection():
    loop = LoopState()
    signal = ProgressSignal(
        reason="repeated_observation",
        round_count=2,
        detail="same result",
    )
    observation = ToolObservation(
        tool_name="search",
        arguments='{"query":"x"}',
        content="no matches",
        success=True,
        retryable=False,
    )

    loop.record_model_call()
    loop.record_tool_round(
        [{"id": "call_1", "function": {"name": "search"}}],
        [observation],
    )
    loop.record_reflection(signal)

    assert LoopState.scope is StateScope.LOOP
    assert LoopState.policy().owner == "AgentLoop"
    assert loop.model_iterations == 1
    assert loop.tool_iterations == 1
    assert loop.reflection_count == 1
    assert loop.phase is LoopPhase.REFLECTING
    assert loop.transition is LoopTransition.REFLECTION_RETRY
    assert loop.needs_follow_up is True
    assert loop.progress_signal is signal
    assert loop.observations == [observation]
    assert [item["to"] for item in loop.transition_history] == [
        "preparing",
        "calling_model",
        "validating_tool_calls",
        "executing_tools",
        "processing_observations",
        "reflecting",
    ]


def test_loop_state_stop_hook_flags_are_separate_from_reflection():
    loop = LoopState()
    observation = ToolObservation(
        tool_name="search",
        arguments="{}",
        content="ok",
        success=True,
        retryable=False,
    )

    loop.record_model_call()
    loop.record_tool_round([], [observation])
    loop.activate_stop_hook()
    loop.activate_stop_hook()
    loop.clear_stop_hook()

    assert loop.stop_hook_active is False
    assert loop.stop_hook_attempts == 2
    assert loop.reflection_count == 0
    assert loop.phase is LoopPhase.RUNNING_STOP_HOOKS
    assert loop.transition is LoopTransition.STOP_HOOK_RETRY


def test_loop_state_rejects_illegal_transition_from_idle_to_tools():
    loop = LoopState()

    with pytest.raises(LoopTransitionError, match="idle -> executing_tools"):
        loop.transition_to(
            LoopPhase.EXECUTING_TOOLS,
            LoopTransition.TOOL_EXECUTION,
        )


def test_loop_state_maps_progress_and_duplicate_reasons_to_formal_transitions():
    loop = LoopState()
    signal = ProgressSignal(
        reason="repeating_tool_cycle",
        round_count=3,
        detail="cycle",
    )

    loop.record_model_call()
    loop.validate_tool_calls([{"id": "call_1", "function": {"name": "search"}}])
    loop.record_progress_signal(signal)
    loop.record_reflection(signal)

    assert loop.progress_signal is signal
    assert loop.transition is LoopTransition.REFLECTION_RETRY
    assert loop.transition_history[-2]["reason"] == LoopTransition.NO_PROGRESS.value
    assert loop.transition_history[-1]["reason"] == LoopTransition.REFLECTION_RETRY.value

    loop.mark_blocked("repeated_tool_call")

    assert loop.phase is LoopPhase.BLOCKED
    assert loop.transition is LoopTransition.DUPLICATE_CALL_LIMIT


def test_agent_loop_populates_turn_and_loop_state_for_simple_reply():
    registry = ToolRegistry()
    loop = AgentLoop(
        client=MagicMock(),
        model="test-model",
        system_prompt="sys",
        registry=registry,
        store=FakeStore(),
    )
    loop.client.chat.completions.create.return_value = make_response(content="hello")

    result = loop.run("hi", stream=False)

    assert result == "hello"
    assert loop.last_turn_state is not None
    assert loop.last_turn_state.status is TurnStatus.COMPLETED
    assert loop.last_turn_state.final_response == "hello"
    assert loop.last_loop_state is not None
    assert loop.last_loop_state.model_iterations == 1
    assert loop.last_loop_state.tool_iterations == 0
    assert loop.last_loop_state.phase is LoopPhase.COMPLETED
    assert loop.last_message_store is not None
    assert loop.last_message_store.pending_messages == []
    assert loop.last_message_store.committed_messages == [
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": "hello"},
    ]


def test_agent_loop_populates_loop_state_for_tool_round():
    registry = ToolRegistry()

    @registry.tool(description="echo")
    def echo(text: str) -> str:
        return f"ECHO:{text}"

    loop = AgentLoop(
        client=MagicMock(),
        model="test-model",
        system_prompt="sys",
        registry=registry,
        store=FakeStore(),
    )
    loop.client.chat.completions.create.side_effect = [
        make_response(tool_calls=[make_tool_call("echo", {"text": "world"})]),
        make_response(content="done"),
    ]

    result = loop.run("call echo", stream=False)

    assert result == "done"
    assert loop.last_turn_state is not None
    assert loop.last_turn_state.status is TurnStatus.COMPLETED
    assert loop.last_loop_state is not None
    assert loop.last_loop_state.model_iterations == 2
    assert loop.last_loop_state.tool_iterations == 1
    assert loop.last_loop_state.phase is LoopPhase.COMPLETED
    assert loop.last_loop_state.needs_follow_up is False
    assert len(loop.last_loop_state.observations) == 1
    assert loop.last_loop_state.observations[0].content == "ECHO:world"
    assert loop.last_loop_state.transition_history[-1]["to"] == "completed"
    assert loop.last_message_store is not None
    assert [message["role"] for message in loop.last_message_store.committed_messages] == [
        "user",
        "assistant",
        "tool",
        "assistant",
    ]


def test_agent_loop_records_permission_wait_and_denial_state():
    registry = ToolRegistry()

    @registry.tool(description="danger", risk_level="high")
    def dangerous() -> str:
        return "should not run"

    loop = AgentLoop(
        client=MagicMock(),
        model="test-model",
        system_prompt="sys",
        registry=registry,
        store=FakeStore(),
        confirm_tool=lambda name, arguments, risk: False,
    )
    loop.client.chat.completions.create.side_effect = [
        make_response(tool_calls=[make_tool_call("dangerous", {})]),
        make_response(content="denied handled"),
    ]

    result = loop.run("call dangerous", stream=False)

    assert result == "denied handled"
    assert loop.last_loop_state is not None
    reasons = [item["reason"] for item in loop.last_loop_state.transition_history]
    assert LoopTransition.PERMISSION_REQUESTED.value in reasons
    assert LoopTransition.PERMISSION_DENIED.value in reasons
    assert loop.last_loop_state.observations[0].content == "User denied tool execution."
    assert loop.last_turn_state is not None
    assert loop.last_turn_state.permission_requests[0]["tool_name"] == "dangerous"
    assert loop.last_turn_state.permission_denials[0]["status"] == "denied"
    assert loop.permission_state.denials[0].tool_call_id == "call_1"


def test_agent_loop_records_permission_approval_decision():
    registry = ToolRegistry()

    @registry.tool(description="danger", risk_level="high")
    def dangerous() -> str:
        return "approved result"

    loop = AgentLoop(
        client=MagicMock(),
        model="test-model",
        system_prompt="sys",
        registry=registry,
        store=FakeStore(),
        confirm_tool=lambda name, arguments, risk: True,
    )
    loop.client.chat.completions.create.side_effect = [
        make_response(tool_calls=[make_tool_call("dangerous", {})]),
        make_response(content="done"),
    ]

    result = loop.run("call dangerous", stream=False)

    assert result == "done"
    assert loop.permission_state.decisions[0].status is PermissionStatus.APPROVED_ONCE
    assert loop.permission_state.denials == []
    assert loop.last_turn_state is not None
    assert loop.last_turn_state.permission_requests[0]["tool_name"] == "dangerous"


def test_agent_loop_honors_pre_cancelled_session_before_model_call():
    controller = CancellationController(session_id="session-a")
    controller.cancel_session("user_interrupt")
    client = MagicMock()
    loop = AgentLoop(
        client=client,
        model="test-model",
        system_prompt="sys",
        registry=ToolRegistry(),
        store=FakeStore(),
        cancellation_controller=controller,
    )

    result = loop.run("hi", stream=False)

    assert result == ""
    assert client.chat.completions.create.call_count == 0
    assert loop.last_turn_state is not None
    assert loop.last_turn_state.status is TurnStatus.CANCELLED
    assert loop.last_turn_state.error == "user_interrupt"


def test_agent_loop_cancels_before_sync_tool_execution():
    registry = ToolRegistry()
    controller = CancellationController(session_id="session-a")

    @registry.tool(description="never called")
    def dangerous() -> str:
        raise AssertionError("tool should not execute after cancellation")

    def cancel_on_tool_start(_event):
        controller.cancel_session("tool_cancelled")

    loop = AgentLoop(
        client=MagicMock(),
        model="test-model",
        system_prompt="sys",
        registry=registry,
        store=FakeStore(),
        cancellation_controller=controller,
        on_tool_start=cancel_on_tool_start,
    )
    loop.client.chat.completions.create.return_value = make_response(
        tool_calls=[make_tool_call("dangerous", {})],
    )

    result = loop.run("call dangerous", stream=False)

    assert result == ""
    assert loop.last_turn_state is not None
    assert loop.last_turn_state.status is TurnStatus.CANCELLED
    assert loop.last_turn_state.error == "tool_cancelled"
    assert loop.last_loop_state is not None
    assert loop.last_loop_state.phase is LoopPhase.CANCELLED


def test_agent_loop_keeps_reflection_prompt_out_of_persisted_messages():
    registry = ToolRegistry()

    @registry.tool(description="echo")
    def echo(text: str) -> str:
        return f"ECHO:{text}"

    loop = AgentLoop(
        client=MagicMock(),
        model="test-model",
        system_prompt="sys",
        registry=registry,
        store=FakeStore(),
        duplicate_tool_call_limit=1,
    )
    loop.client.chat.completions.create.side_effect = [
        make_response(tool_calls=[make_tool_call("echo", {"text": "same"})]),
        make_response(content="changed approach"),
    ]

    result = loop.run("call echo", stream=False)

    assert result == "changed approach"
    assert loop.last_message_store is not None
    runtime_contents = [
        message.get("content")
        for message in loop.last_message_store.runtime_messages
        if message.get("content")
    ]
    committed_contents = [
        message.get("content")
        for message in loop.last_message_store.committed_messages
        if message.get("content")
    ]
    assert any("[REFLECTION_REQUIRED]" in content for content in runtime_contents)
    assert not any("[REFLECTION_REQUIRED]" in content for content in committed_contents)
