from zzm_agent.core.state_lifecycle import (
    PersistenceBoundary,
    RecoveryStrategy,
    StateLifetime,
    StateScope,
    get_state_policy,
    state_children,
    state_lineage,
    validate_state_lifecycle_policies,
)


def test_state_lifecycle_policy_table_is_complete_and_valid():
    assert validate_state_lifecycle_policies() == []


def test_state_lineage_defines_runtime_ownership_tree():
    assert state_lineage(StateScope.LOOP) == (
        StateScope.APPLICATION,
        StateScope.CONVERSATION,
        StateScope.TURN,
        StateScope.LOOP,
    )
    assert state_lineage(StateScope.WORKING_MEMORY) == (
        StateScope.APPLICATION,
        StateScope.CONVERSATION,
        StateScope.TASK,
        StateScope.WORKING_MEMORY,
    )


def test_conversation_owns_turns_and_tasks():
    assert state_children(StateScope.CONVERSATION) == (
        StateScope.TURN,
        StateScope.TASK,
    )


def test_application_state_is_process_level_and_recreated_on_startup():
    policy = get_state_policy("application")

    assert policy.parent is None
    assert policy.lifetime is StateLifetime.PROCESS
    assert policy.owner == "ApplicationRuntime"
    assert policy.persistence is PersistenceBoundary.MEMORY_ONLY
    assert policy.recovery is RecoveryStrategy.RECREATE


def test_conversation_state_is_session_persisted_and_resumable():
    policy = get_state_policy(StateScope.CONVERSATION)

    assert policy.parent is StateScope.APPLICATION
    assert policy.lifetime is StateLifetime.SESSION
    assert policy.owner == "QueryEngine"
    assert policy.persistence is PersistenceBoundary.SESSION_STORE
    assert policy.recovery is RecoveryStrategy.RESUME
    assert "MemoryStore" in policy.allowed_writers


def test_turn_and_loop_have_different_recovery_boundaries():
    turn = get_state_policy(StateScope.TURN)
    loop = get_state_policy(StateScope.LOOP)

    assert turn.parent is StateScope.CONVERSATION
    assert turn.recovery is RecoveryStrategy.ROLLBACK_PENDING
    assert turn.persistence is PersistenceBoundary.CHECKPOINT_STORE
    assert loop.parent is StateScope.TURN
    assert loop.owner == "AgentLoop"
    assert loop.recovery is RecoveryStrategy.DISCARD


def test_task_and_working_memory_are_planner_owned_task_scopes():
    task = get_state_policy(StateScope.TASK)
    memory = get_state_policy(StateScope.WORKING_MEMORY)

    assert task.parent is StateScope.CONVERSATION
    assert task.owner == "Planner"
    assert task.persistence is PersistenceBoundary.TASK_STORE
    assert task.recovery is RecoveryStrategy.CHECKPOINT
    assert memory.parent is StateScope.TASK
    assert memory.owner == "Planner"
    assert memory.persistence is PersistenceBoundary.TASK_STORE
