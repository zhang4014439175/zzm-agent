 from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, ClassVar
from uuid import uuid4

from zzm_agent.core.observability import TokenUsage
from zzm_agent.core.progress_monitor import ProgressSignal, ToolObservation
from zzm_agent.core.state_lifecycle import (
    StateLifecyclePolicy,
    StateScope,
    get_state_policy,
)


class TurnStatus(str, Enum):
    """Lifecycle status for one user turn."""

    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    BLOCKED = "blocked"
    CANCELLED = "cancelled"
    FAILED = "failed"


class LoopPhase(str, Enum):
    """Minimal loop phases used until the formal 6.3 state machine lands."""

    IDLE = "idle"
    CALLING_MODEL = "calling_model"
    EXECUTING_TOOLS = "executing_tools"
    REFLECTING = "reflecting"
    COMPLETED = "completed"
    BLOCKED = "blocked"
    CANCELLED = "cancelled"
    FAILED = "failed"


@dataclass
class LoopState:
    """Runtime state owned by AgentLoop for one ReAct loop."""

    scope: ClassVar[StateScope] = StateScope.LOOP

    phase: LoopPhase | str = LoopPhase.IDLE
    transition: str | None = None
    model_iterations: int = 0
    tool_iterations: int = 0
    reflection_count: int = 0
    current_tool_calls: list[dict[str, Any]] = field(default_factory=list)
    observations: list[ToolObservation] = field(default_factory=list)
    progress_signal: ProgressSignal | None = None
    needs_follow_up: bool = False
    stop_hook_active: bool = False
    stop_hook_attempts: int = 0

    @classmethod
    def policy(cls) -> StateLifecyclePolicy:
        return get_state_policy(cls.scope)

    def transition_to(self, phase: LoopPhase | str, reason: str | None = None) -> None:
        self.phase = phase
        self.transition = reason

    def record_model_call(self) -> None:
        self.model_iterations += 1
        self.transition_to(LoopPhase.CALLING_MODEL, "model_call")

    def record_tool_round(
        self,
        tool_calls: list[dict[str, Any]],
        observations: list[ToolObservation],
    ) -> None:
        self.tool_iterations += 1
        self.current_tool_calls = list(tool_calls)
        self.observations.extend(observations)
        self.needs_follow_up = True
        self.transition_to(LoopPhase.EXECUTING_TOOLS, "tool_round")

    def record_progress_signal(self, signal: ProgressSignal) -> None:
        self.progress_signal = signal
        self.transition = signal.reason

    def record_reflection(self, signal: ProgressSignal) -> None:
        self.reflection_count += 1
        self.progress_signal = signal
        self.transition_to(LoopPhase.REFLECTING, "reflection_retry")

    def mark_completed(self, reason: str = "completed") -> None:
        self.needs_follow_up = False
        self.current_tool_calls = []
        self.transition_to(LoopPhase.COMPLETED, reason)

    def mark_blocked(self, reason: str = "blocked") -> None:
        self.needs_follow_up = False
        self.current_tool_calls = []
        self.transition_to(LoopPhase.BLOCKED, reason)

    def activate_stop_hook(self) -> None:
        self.stop_hook_active = True
        self.stop_hook_attempts += 1

    def clear_stop_hook(self) -> None:
        self.stop_hook_active = False


@dataclass
class TurnState:
    """Runtime state for one submitted user message."""

    user_input: str
    turn_id: str = field(default_factory=lambda: f"turn-{uuid4().hex[:8]}")
    status: TurnStatus | str = TurnStatus.PENDING
    usage: TokenUsage = field(default_factory=TokenUsage)
    discovered_skills: set[str] = field(default_factory=set)
    loaded_memory_paths: set[str] = field(default_factory=set)
    permission_requests: list[dict[str, Any]] = field(default_factory=list)
    permission_denials: list[dict[str, Any]] = field(default_factory=list)
    artifacts: list[dict[str, Any]] = field(default_factory=list)
    loop: LoopState | None = None
    final_response: str | None = None
    error: str | None = None

    scope: ClassVar[StateScope] = StateScope.TURN

    @classmethod
    def policy(cls) -> StateLifecyclePolicy:
        return get_state_policy(cls.scope)

    def start(self) -> None:
        self.status = TurnStatus.IN_PROGRESS

    def start_loop(self) -> LoopState:
        self.loop = LoopState()
        self.status = TurnStatus.IN_PROGRESS
        return self.loop

    def add_usage(self, usage: TokenUsage) -> None:
        self.usage.add(usage)

    def complete(self, final_response: str, usage: TokenUsage | None = None) -> None:
        if usage is not None:
            self.usage = usage.copy()
        self.final_response = final_response
        self.error = None
        self.status = TurnStatus.COMPLETED
        if self.loop is not None:
            self.loop.mark_completed()

    def block(self, final_response: str, reason: str = "blocked") -> None:
        self.final_response = final_response
        self.error = reason
        self.status = TurnStatus.BLOCKED
        if self.loop is not None:
            self.loop.mark_blocked(reason)

    def fail(self, error: str) -> None:
        self.error = error
        self.status = TurnStatus.FAILED
        if self.loop is not None:
            self.loop.transition_to(LoopPhase.FAILED, "error")

    def cancel(self, reason: str = "cancelled") -> None:
        self.error = reason
        self.status = TurnStatus.CANCELLED
        if self.loop is not None:
            self.loop.transition_to(LoopPhase.CANCELLED, reason)


@dataclass
class ConversationState:
    """Cross-turn state for one conversation/session."""

    session_id: str
    messages: list[dict[str, Any]] = field(default_factory=list)
    usage: TokenUsage = field(default_factory=TokenUsage)
    permissions: dict[str, Any] = field(default_factory=dict)
    file_reads: dict[str, Any] = field(default_factory=dict)
    skills: set[str] = field(default_factory=set)
    memories: dict[str, Any] = field(default_factory=dict)
    cancellation: dict[str, Any] = field(default_factory=dict)
    active_turn: TurnState | None = None
    active_task: Any | None = None

    scope: ClassVar[StateScope] = StateScope.CONVERSATION

    @classmethod
    def policy(cls) -> StateLifecyclePolicy:
        return get_state_policy(cls.scope)

    def append_messages(self, messages: list[dict[str, Any]]) -> None:
        self.messages.extend(messages)

    def start_turn(self, user_input: str, turn_id: str | None = None) -> TurnState:
        if self.active_turn is not None and self.active_turn.status in {
            TurnStatus.PENDING,
            TurnStatus.IN_PROGRESS,
        }:
            raise RuntimeError("Cannot start a new turn while another turn is active.")
        self.active_turn = TurnState(
            turn_id=turn_id or f"turn-{uuid4().hex[:8]}",
            user_input=user_input,
        )
        self.active_turn.start()
        return self.active_turn

    def finish_turn(
        self,
        final_response: str,
        *,
        messages: list[dict[str, Any]] | None = None,
        usage: TokenUsage | None = None,
    ) -> TurnState:
        if self.active_turn is None:
            raise RuntimeError("No active turn to finish.")
        self.active_turn.complete(final_response, usage=usage)
        if usage is not None:
            self.usage.add(usage)
        if messages:
            self.append_messages(messages)
        finished = self.active_turn
        self.active_turn = None
        return finished

    def fail_turn(self, error: str) -> TurnState:
        if self.active_turn is None:
            raise RuntimeError("No active turn to fail.")
        self.active_turn.fail(error)
        failed = self.active_turn
        self.active_turn = None
        return failed


@dataclass
class ApplicationState:
    """Process-level state owned by the application runtime."""

    configuration: dict[str, Any] = field(default_factory=dict)
    model_registry: dict[str, Any] = field(default_factory=dict)
    tool_registry: Any | None = None
    skill_registry: dict[str, Any] = field(default_factory=dict)
    mcp_connections: dict[str, Any] = field(default_factory=dict)
    active_session_id: str | None = None
    conversations: dict[str, ConversationState] = field(default_factory=dict)

    scope: ClassVar[StateScope] = StateScope.APPLICATION

    @classmethod
    def policy(cls) -> StateLifecyclePolicy:
        return get_state_policy(cls.scope)

    def set_active_session(self, session_id: str) -> None:
        self.active_session_id = session_id

    def get_or_create_conversation(self, session_id: str) -> ConversationState:
        if session_id not in self.conversations:
            self.conversations[session_id] = ConversationState(session_id=session_id)
        self.active_session_id = session_id
        return self.conversations[session_id]

    def active_conversation(self) -> ConversationState | None:
        if self.active_session_id is None:
            return None
        return self.conversations.get(self.active_session_id)
