from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class StateScope(str, Enum):
    """Named runtime state scopes owned by the future Conversation Runtime."""

    APPLICATION = "application"
    CONVERSATION = "conversation"
    TURN = "turn"
    LOOP = "loop"
    TASK = "task"
    WORKING_MEMORY = "working_memory"


class StateLifetime(str, Enum):
    """How long a state scope is expected to exist."""

    PROCESS = "process"
    SESSION = "session"
    USER_TURN = "user_turn"
    REACT_LOOP = "react_loop"
    LONG_TASK = "long_task"


class PersistenceBoundary(str, Enum):
    """Where a state scope is allowed to persist."""

    MEMORY_ONLY = "memory_only"
    SESSION_STORE = "session_store"
    TASK_STORE = "task_store"
    CHECKPOINT_STORE = "checkpoint_store"


class RecoveryStrategy(str, Enum):
    """How a state scope should be recovered after interruption or restart."""

    RECREATE = "recreate"
    RESUME = "resume"
    ROLLBACK_PENDING = "rollback_pending"
    CHECKPOINT = "checkpoint"
    DISCARD = "discard"


@dataclass(frozen=True)
class StateLifecyclePolicy:
    """Lifecycle and ownership contract for one runtime state scope."""

    scope: StateScope
    parent: StateScope | None
    lifetime: StateLifetime
    owner: str
    allowed_writers: tuple[str, ...]
    persistence: PersistenceBoundary
    recovery: RecoveryStrategy
    created_by: str
    destroyed_when: str
    purpose: str


STATE_LIFECYCLE_POLICIES: dict[StateScope, StateLifecyclePolicy] = {
    StateScope.APPLICATION: StateLifecyclePolicy(
        scope=StateScope.APPLICATION,
        parent=None,
        lifetime=StateLifetime.PROCESS,
        owner="ApplicationRuntime",
        allowed_writers=("ApplicationRuntime",),
        persistence=PersistenceBoundary.MEMORY_ONLY,
        recovery=RecoveryStrategy.RECREATE,
        created_by="CLI/runtime startup",
        destroyed_when="process exits",
        purpose=(
            "Own process-level configuration, model registry, tool registry, "
            "skill registry, MCP connections, and the active conversation id."
        ),
    ),
    StateScope.CONVERSATION: StateLifecyclePolicy(
        scope=StateScope.CONVERSATION,
        parent=StateScope.APPLICATION,
        lifetime=StateLifetime.SESSION,
        owner="QueryEngine",
        allowed_writers=("QueryEngine", "MemoryStore", "PermissionManager"),
        persistence=PersistenceBoundary.SESSION_STORE,
        recovery=RecoveryStrategy.RESUME,
        created_by="QueryEngine when opening or creating a session",
        destroyed_when="session is deleted or archived",
        purpose=(
            "Own cross-turn conversation data such as persisted messages, "
            "usage totals, permissions, file cache, memory load state, and "
            "the active turn/task references."
        ),
    ),
    StateScope.TURN: StateLifecyclePolicy(
        scope=StateScope.TURN,
        parent=StateScope.CONVERSATION,
        lifetime=StateLifetime.USER_TURN,
        owner="QueryEngine",
        allowed_writers=("QueryEngine", "AgentLoop"),
        persistence=PersistenceBoundary.CHECKPOINT_STORE,
        recovery=RecoveryStrategy.ROLLBACK_PENDING,
        created_by="QueryEngine when a user message is submitted",
        destroyed_when="final response, cancellation, failure, or rollback completes",
        purpose=(
            "Own one user input lifecycle, including pending messages, turn "
            "usage, discovered skills, artifacts, permission requests, and the "
            "LoopState for the active ReAct execution."
        ),
    ),
    StateScope.LOOP: StateLifecyclePolicy(
        scope=StateScope.LOOP,
        parent=StateScope.TURN,
        lifetime=StateLifetime.REACT_LOOP,
        owner="AgentLoop",
        allowed_writers=("AgentLoop",),
        persistence=PersistenceBoundary.CHECKPOINT_STORE,
        recovery=RecoveryStrategy.DISCARD,
        created_by="AgentLoop when executing a turn",
        destroyed_when="turn reaches completed, blocked, cancelled, or failed",
        purpose=(
            "Own intra-turn ReAct state such as phase, transition, model/tool "
            "iterations, tool calls, observations, progress signals, and "
            "reflection counters."
        ),
    ),
    StateScope.TASK: StateLifecyclePolicy(
        scope=StateScope.TASK,
        parent=StateScope.CONVERSATION,
        lifetime=StateLifetime.LONG_TASK,
        owner="Planner",
        allowed_writers=("Planner", "QueryEngine"),
        persistence=PersistenceBoundary.TASK_STORE,
        recovery=RecoveryStrategy.CHECKPOINT,
        created_by="Planner when a long-running goal is accepted",
        destroyed_when="task is completed, cancelled, archived, or explicitly deleted",
        purpose=(
            "Own long-task plan state, step statuses, findings, artifacts, "
            "blockers, and links to child turns."
        ),
    ),
    StateScope.WORKING_MEMORY: StateLifecyclePolicy(
        scope=StateScope.WORKING_MEMORY,
        parent=StateScope.TASK,
        lifetime=StateLifetime.LONG_TASK,
        owner="Planner",
        allowed_writers=("Planner", "TaskExecutor"),
        persistence=PersistenceBoundary.TASK_STORE,
        recovery=RecoveryStrategy.CHECKPOINT,
        created_by="Planner when TaskState is created",
        destroyed_when="parent task is completed, archived, or deleted",
        purpose=(
            "Own task-local notes, confirmed findings, short plans, and "
            "compressed sub-step summaries without polluting long-term memory."
        ),
    ),
}


STATE_PARENT_ORDER: tuple[StateScope, ...] = (
    StateScope.APPLICATION,
    StateScope.CONVERSATION,
    StateScope.TURN,
    StateScope.LOOP,
    StateScope.TASK,
    StateScope.WORKING_MEMORY,
)


def get_state_policy(scope: StateScope | str) -> StateLifecyclePolicy:
    """Return the lifecycle policy for one state scope."""
    normalized = StateScope(scope)
    return STATE_LIFECYCLE_POLICIES[normalized]


def state_lineage(scope: StateScope | str) -> tuple[StateScope, ...]:
    """Return the root-to-scope ownership lineage."""
    current = StateScope(scope)
    lineage: list[StateScope] = []
    while True:
        lineage.append(current)
        parent = STATE_LIFECYCLE_POLICIES[current].parent
        if parent is None:
            break
        current = parent
    return tuple(reversed(lineage))


def state_children(scope: StateScope | str) -> tuple[StateScope, ...]:
    """Return direct child scopes for one state scope."""
    parent = StateScope(scope)
    return tuple(
        candidate.scope
        for candidate in STATE_LIFECYCLE_POLICIES.values()
        if candidate.parent == parent
    )


def validate_state_lifecycle_policies() -> list[str]:
    """Return human-readable validation errors for the lifecycle policy table."""
    errors: list[str] = []
    for scope in StateScope:
        if scope not in STATE_LIFECYCLE_POLICIES:
            errors.append(f"Missing lifecycle policy for {scope.value}.")

    for scope, policy in STATE_LIFECYCLE_POLICIES.items():
        if policy.scope != scope:
            errors.append(f"Policy key {scope.value} does not match policy.scope.")
        if policy.parent == scope:
            errors.append(f"{scope.value} cannot be its own parent.")
        if not policy.owner:
            errors.append(f"{scope.value} must define an owner.")
        if policy.owner not in policy.allowed_writers:
            errors.append(f"{scope.value} owner must be an allowed writer.")
        if not policy.created_by:
            errors.append(f"{scope.value} must define who creates it.")
        if not policy.destroyed_when:
            errors.append(f"{scope.value} must define when it is destroyed.")
        if policy.parent is not None and policy.parent not in STATE_LIFECYCLE_POLICIES:
            errors.append(f"{scope.value} references unknown parent {policy.parent}.")

    for scope in STATE_LIFECYCLE_POLICIES:
        seen: set[StateScope] = set()
        current: StateScope | None = scope
        while current is not None:
            if current in seen:
                errors.append(f"Cycle detected in lineage for {scope.value}.")
                break
            seen.add(current)
            current = STATE_LIFECYCLE_POLICIES[current].parent

    return errors
