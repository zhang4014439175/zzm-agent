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
    """Formal phases for one ReAct loop.

    The phase describes where the loop is right now, while LoopTransition
    describes why it moved there.
    """

    IDLE = "idle"
    PREPARING = "preparing"
    CALLING_MODEL = "calling_model"
    STREAMING_RESPONSE = "streaming_response"
    VALIDATING_TOOL_CALLS = "validating_tool_calls"
    AWAITING_PERMISSION = "awaiting_permission"
    EXECUTING_TOOLS = "executing_tools"
    PROCESSING_OBSERVATIONS = "processing_observations"
    REFLECTING = "reflecting"
    RUNNING_STOP_HOOKS = "running_stop_hooks"
    COMPLETED = "completed"
    BLOCKED = "blocked"
    CANCELLED = "cancelled"
    FAILED = "failed"


class LoopTransition(str, Enum):
    """Reason for a LoopPhase transition."""

    NEXT_TURN = "next_turn"
    TOOL_FOLLOW_UP = "tool_follow_up"
    REFLECTION_RETRY = "reflection_retry"
    STOP_HOOK_RETRY = "stop_hook_retry"
    COMPLETED = "completed"
    NO_PROGRESS = "no_progress"
    ITERATION_LIMIT = "iteration_limit"
    DUPLICATE_CALL_LIMIT = "duplicate_call_limit"
    PERMISSION_DENIED = "permission_denied"
    BLOCKED = "blocked"
    CANCELLED = "cancelled"
    ERROR = "error"
    STREAM_RESPONSE = "stream_response"
    TOOL_VALIDATION = "tool_validation"
    PERMISSION_REQUESTED = "permission_requested"
    TOOL_EXECUTION = "tool_execution"
    OBSERVATION = "observation"


class LoopTransitionError(RuntimeError):
    """Raised when the loop attempts an invalid phase transition."""


_TERMINAL_LOOP_PHASES = {
    LoopPhase.COMPLETED,
    LoopPhase.BLOCKED,
    LoopPhase.CANCELLED,
    LoopPhase.FAILED,
}

_ALLOWED_LOOP_TRANSITIONS: dict[LoopPhase, set[LoopPhase]] = {
    LoopPhase.IDLE: {
        LoopPhase.PREPARING,
        LoopPhase.COMPLETED,
        LoopPhase.BLOCKED,
        LoopPhase.CANCELLED,
        LoopPhase.FAILED,
    },
    LoopPhase.PREPARING: {
        LoopPhase.CALLING_MODEL,
        LoopPhase.COMPLETED,
        LoopPhase.BLOCKED,
        LoopPhase.CANCELLED,
        LoopPhase.FAILED,
    },
    LoopPhase.CALLING_MODEL: {
        LoopPhase.STREAMING_RESPONSE,
        LoopPhase.VALIDATING_TOOL_CALLS,
        LoopPhase.COMPLETED,
        LoopPhase.BLOCKED,
        LoopPhase.CANCELLED,
        LoopPhase.FAILED,
    },
    LoopPhase.STREAMING_RESPONSE: {
        LoopPhase.VALIDATING_TOOL_CALLS,
        LoopPhase.COMPLETED,
        LoopPhase.BLOCKED,
        LoopPhase.CANCELLED,
        LoopPhase.FAILED,
    },
    LoopPhase.VALIDATING_TOOL_CALLS: {
        LoopPhase.AWAITING_PERMISSION,
        LoopPhase.EXECUTING_TOOLS,
        LoopPhase.PROCESSING_OBSERVATIONS,
        LoopPhase.REFLECTING,
        LoopPhase.BLOCKED,
        LoopPhase.CANCELLED,
        LoopPhase.FAILED,
    },
    LoopPhase.AWAITING_PERMISSION: {
        LoopPhase.EXECUTING_TOOLS,
        LoopPhase.PROCESSING_OBSERVATIONS,
        LoopPhase.REFLECTING,
        LoopPhase.BLOCKED,
        LoopPhase.CANCELLED,
        LoopPhase.FAILED,
    },
    LoopPhase.EXECUTING_TOOLS: {
        LoopPhase.AWAITING_PERMISSION,
        LoopPhase.PROCESSING_OBSERVATIONS,
        LoopPhase.REFLECTING,
        LoopPhase.BLOCKED,
        LoopPhase.CANCELLED,
        LoopPhase.FAILED,
    },
    LoopPhase.PROCESSING_OBSERVATIONS: {
        LoopPhase.CALLING_MODEL,
        LoopPhase.REFLECTING,
        LoopPhase.RUNNING_STOP_HOOKS,
        LoopPhase.COMPLETED,
        LoopPhase.BLOCKED,
        LoopPhase.CANCELLED,
        LoopPhase.FAILED,
    },
    LoopPhase.REFLECTING: {
        LoopPhase.CALLING_MODEL,
        LoopPhase.BLOCKED,
        LoopPhase.CANCELLED,
        LoopPhase.FAILED,
    },
    LoopPhase.RUNNING_STOP_HOOKS: {
        LoopPhase.CALLING_MODEL,
        LoopPhase.COMPLETED,
        LoopPhase.BLOCKED,
        LoopPhase.CANCELLED,
        LoopPhase.FAILED,
    },
    LoopPhase.COMPLETED: set(),
    LoopPhase.BLOCKED: set(),
    LoopPhase.CANCELLED: set(),
    LoopPhase.FAILED: set(),
}

_TRANSITION_REASON_ALIASES = {
    "model_call": LoopTransition.NEXT_TURN,
    "tool_round": LoopTransition.OBSERVATION,
    "interrupted": LoopTransition.CANCELLED,
    "repeated_tool_call": LoopTransition.DUPLICATE_CALL_LIMIT,
    "repeated_observation": LoopTransition.NO_PROGRESS,
    "repeating_tool_cycle": LoopTransition.NO_PROGRESS,
    "consecutive_non_retryable_failures": LoopTransition.NO_PROGRESS,
}


def _coerce_loop_phase(phase: LoopPhase | str) -> LoopPhase:
    if isinstance(phase, LoopPhase):
        return phase
    return LoopPhase(str(phase))


def _coerce_loop_transition(
    reason: LoopTransition | str | None,
) -> LoopTransition | str | None:
    if reason is None or isinstance(reason, LoopTransition):
        return reason
    text = str(reason)
    if text in _TRANSITION_REASON_ALIASES:
        return _TRANSITION_REASON_ALIASES[text]
    try:
        return LoopTransition(text)
    except ValueError:
        return text


@dataclass
class LoopState:
    """Runtime state owned by AgentLoop for one ReAct loop."""

    scope: ClassVar[StateScope] = StateScope.LOOP

    phase: LoopPhase = LoopPhase.IDLE
    transition: LoopTransition | str | None = None
    transition_history: list[dict[str, str | None]] = field(default_factory=list)
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

    def can_transition_to(self, phase: LoopPhase | str) -> bool:
        next_phase = _coerce_loop_phase(phase)
        current_phase = _coerce_loop_phase(self.phase)
        if current_phase == next_phase:
            return True
        return next_phase in _ALLOWED_LOOP_TRANSITIONS[current_phase]

    def transition_to(
        self,
        phase: LoopPhase | str,
        reason: LoopTransition | str | None = None,
    ) -> None:
        current_phase = _coerce_loop_phase(self.phase)
        next_phase = _coerce_loop_phase(phase)
        transition_reason = _coerce_loop_transition(reason)
        if not self.can_transition_to(next_phase):
            raise LoopTransitionError(
                f"Invalid loop transition: {current_phase.value} -> {next_phase.value}"
            )
        self.phase = next_phase
        self.transition = transition_reason
        self.transition_history.append(
            {
                "from": current_phase.value,
                "to": next_phase.value,
                "reason": (
                    transition_reason.value
                    if isinstance(transition_reason, LoopTransition)
                    else transition_reason
                ),
            }
        )

    def prepare_next_turn(self) -> None:
        self.transition_to(LoopPhase.PREPARING, LoopTransition.NEXT_TURN)

    def record_model_call(self) -> None:
        if self.phase == LoopPhase.IDLE:
            self.prepare_next_turn()
        reason = self._next_model_call_reason()
        self.model_iterations += 1
        self.transition_to(LoopPhase.CALLING_MODEL, reason)

    def record_streaming_response(self) -> None:
        self.transition_to(LoopPhase.STREAMING_RESPONSE, LoopTransition.STREAM_RESPONSE)

    def validate_tool_calls(self, tool_calls: list[dict[str, Any]]) -> None:
        self.current_tool_calls = list(tool_calls)
        self.transition_to(
            LoopPhase.VALIDATING_TOOL_CALLS,
            LoopTransition.TOOL_VALIDATION,
        )

    def await_permission(self) -> None:
        self.transition_to(
            LoopPhase.AWAITING_PERMISSION,
            LoopTransition.PERMISSION_REQUESTED,
        )

    def record_permission_denial(self) -> None:
        self.transition_to(
            LoopPhase.AWAITING_PERMISSION,
            LoopTransition.PERMISSION_DENIED,
        )

    def record_tool_execution_start(
        self,
        tool_calls: list[dict[str, Any]] | None = None,
    ) -> None:
        if tool_calls is not None:
            self.current_tool_calls = list(tool_calls)
        self.transition_to(LoopPhase.EXECUTING_TOOLS, LoopTransition.TOOL_EXECUTION)

    def record_tool_round(
        self,
        tool_calls: list[dict[str, Any]],
        observations: list[ToolObservation],
    ) -> None:
        if self.phase in {LoopPhase.CALLING_MODEL, LoopPhase.STREAMING_RESPONSE}:
            self.validate_tool_calls(tool_calls)
        if self.phase == LoopPhase.VALIDATING_TOOL_CALLS:
            self.record_tool_execution_start(tool_calls)
        self.tool_iterations += 1
        self.current_tool_calls = list(tool_calls)
        self.observations.extend(observations)
        self.needs_follow_up = True
        self.transition_to(
            LoopPhase.PROCESSING_OBSERVATIONS,
            LoopTransition.OBSERVATION,
        )

    def record_progress_signal(self, signal: ProgressSignal) -> None:
        self.progress_signal = signal
        self.transition = _coerce_loop_transition(signal.reason)
        self.transition_history.append(
            {
                "from": self.phase.value,
                "to": self.phase.value,
                "reason": (
                    self.transition.value
                    if isinstance(self.transition, LoopTransition)
                    else self.transition
                ),
            }
        )

    def record_reflection(self, signal: ProgressSignal) -> None:
        self.reflection_count += 1
        self.progress_signal = signal
        self.transition_to(LoopPhase.REFLECTING, LoopTransition.REFLECTION_RETRY)

    def mark_completed(
        self,
        reason: LoopTransition | str = LoopTransition.COMPLETED,
    ) -> None:
        self.needs_follow_up = False
        self.current_tool_calls = []
        self.transition_to(LoopPhase.COMPLETED, reason)

    def mark_blocked(
        self,
        reason: LoopTransition | str = LoopTransition.BLOCKED,
    ) -> None:
        self.needs_follow_up = False
        self.current_tool_calls = []
        self.transition_to(LoopPhase.BLOCKED, reason)

    def mark_failed(self, reason: LoopTransition | str = LoopTransition.ERROR) -> None:
        self.needs_follow_up = False
        self.current_tool_calls = []
        self.transition_to(LoopPhase.FAILED, reason)

    def mark_cancelled(
        self,
        reason: LoopTransition | str = LoopTransition.CANCELLED,
    ) -> None:
        self.needs_follow_up = False
        self.current_tool_calls = []
        self.transition_to(LoopPhase.CANCELLED, reason)

    def activate_stop_hook(self) -> None:
        self.stop_hook_active = True
        self.stop_hook_attempts += 1
        self.transition_to(
            LoopPhase.RUNNING_STOP_HOOKS,
            LoopTransition.STOP_HOOK_RETRY,
        )

    def clear_stop_hook(self) -> None:
        self.stop_hook_active = False

    def _next_model_call_reason(self) -> LoopTransition:
        if self.phase == LoopPhase.REFLECTING:
            return LoopTransition.REFLECTION_RETRY
        if self.phase == LoopPhase.RUNNING_STOP_HOOKS:
            return LoopTransition.STOP_HOOK_RETRY
        if self.needs_follow_up or self.phase == LoopPhase.PROCESSING_OBSERVATIONS:
            return LoopTransition.TOOL_FOLLOW_UP
        return LoopTransition.NEXT_TURN


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
        self.loop.prepare_next_turn()
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
            self.loop.mark_failed()

    def cancel(self, reason: str = "cancelled") -> None:
        self.error = reason
        self.status = TurnStatus.CANCELLED
        if self.loop is not None:
            self.loop.mark_cancelled(reason)


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
