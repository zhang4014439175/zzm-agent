from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, ClassVar

from zzm_agent.core.progress_monitor import ProgressSignal, ToolObservation
from zzm_agent.core.state_lifecycle import StateLifecyclePolicy, StateScope, get_state_policy

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
    YIELDED = "yielded"
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
    YIELDED = "yielded"
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
_ALLOWED_LOOP_TRANSITIONS: dict[LoopPhase, set[LoopPhase]] = {
    LoopPhase.IDLE: {
        LoopPhase.PREPARING,
        LoopPhase.COMPLETED,
        LoopPhase.YIELDED,
        LoopPhase.BLOCKED,
        LoopPhase.CANCELLED,
        LoopPhase.FAILED,
    },
    LoopPhase.PREPARING: {
        LoopPhase.CALLING_MODEL,
        LoopPhase.COMPLETED,
        LoopPhase.YIELDED,
        LoopPhase.BLOCKED,
        LoopPhase.CANCELLED,
        LoopPhase.FAILED,
    },
    LoopPhase.CALLING_MODEL: {
        LoopPhase.STREAMING_RESPONSE,
        LoopPhase.VALIDATING_TOOL_CALLS,
        LoopPhase.RUNNING_STOP_HOOKS,
        LoopPhase.COMPLETED,
        LoopPhase.YIELDED,
        LoopPhase.BLOCKED,
        LoopPhase.CANCELLED,
        LoopPhase.FAILED,
    },
    LoopPhase.STREAMING_RESPONSE: {
        LoopPhase.VALIDATING_TOOL_CALLS,
        LoopPhase.RUNNING_STOP_HOOKS,
        LoopPhase.COMPLETED,
        LoopPhase.YIELDED,
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
        LoopPhase.YIELDED,
        LoopPhase.BLOCKED,
        LoopPhase.CANCELLED,
        LoopPhase.FAILED,
    },
    LoopPhase.REFLECTING: {
        LoopPhase.CALLING_MODEL,
        LoopPhase.YIELDED,
        LoopPhase.BLOCKED,
        LoopPhase.CANCELLED,
        LoopPhase.FAILED,
    },
    LoopPhase.RUNNING_STOP_HOOKS: {
        LoopPhase.CALLING_MODEL,
        LoopPhase.COMPLETED,
        LoopPhase.YIELDED,
        LoopPhase.BLOCKED,
        LoopPhase.CANCELLED,
        LoopPhase.FAILED,
    },
    LoopPhase.COMPLETED: set(),
    LoopPhase.YIELDED: set(),
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


def _enum_value(value: Any) -> Any:
    if isinstance(value, Enum):
        return value.value
    return value


def _tool_observation_to_record(observation: ToolObservation) -> dict[str, Any]:
    return {
        "tool_name": observation.tool_name,
        "arguments": observation.arguments,
        "content": observation.content,
        "success": observation.success,
        "retryable": observation.retryable,
    }


def _tool_observation_from_record(record: dict[str, Any]) -> ToolObservation:
    return ToolObservation(
        tool_name=str(record.get("tool_name", "")),
        arguments=str(record.get("arguments", "")),
        content=str(record.get("content", "")),
        success=bool(record.get("success", False)),
        retryable=bool(record.get("retryable", False)),
    )


def _progress_signal_to_record(signal: ProgressSignal | None) -> dict[str, Any] | None:
    if signal is None:
        return None
    return {
        "reason": signal.reason,
        "round_count": signal.round_count,
        "detail": signal.detail,
    }


def _progress_signal_from_record(record: dict[str, Any] | None) -> ProgressSignal | None:
    if not record:
        return None
    return ProgressSignal(
        reason=str(record.get("reason", "")),
        round_count=int(record.get("round_count", 0)),
        detail=str(record.get("detail", "")),
    )


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

    def mark_yielded(
        self,
        reason: LoopTransition | str = LoopTransition.YIELDED,
    ) -> None:
        self.needs_follow_up = True
        self.current_tool_calls = []
        self.transition_to(LoopPhase.YIELDED, reason)

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

    def to_record(self) -> dict[str, Any]:
        return {
            "phase": _enum_value(self.phase),
            "transition": _enum_value(self.transition),
            "transition_history": list(self.transition_history),
            "model_iterations": self.model_iterations,
            "tool_iterations": self.tool_iterations,
            "reflection_count": self.reflection_count,
            "current_tool_calls": list(self.current_tool_calls),
            "observations": [
                _tool_observation_to_record(observation)
                for observation in self.observations
            ],
            "progress_signal": _progress_signal_to_record(self.progress_signal),
            "needs_follow_up": self.needs_follow_up,
            "stop_hook_active": self.stop_hook_active,
            "stop_hook_attempts": self.stop_hook_attempts,
        }

    @classmethod
    def from_record(cls, record: dict[str, Any] | None) -> "LoopState":
        if not record:
            return cls()
        state = cls(
            phase=_coerce_loop_phase(record.get("phase", LoopPhase.IDLE.value)),
            transition=_coerce_loop_transition(record.get("transition")),
            transition_history=[
                dict(item)
                for item in record.get("transition_history", [])
                if isinstance(item, dict)
            ],
            model_iterations=int(record.get("model_iterations", 0)),
            tool_iterations=int(record.get("tool_iterations", 0)),
            reflection_count=int(record.get("reflection_count", 0)),
            current_tool_calls=[
                dict(item)
                for item in record.get("current_tool_calls", [])
                if isinstance(item, dict)
            ],
            observations=[
                _tool_observation_from_record(item)
                for item in record.get("observations", [])
                if isinstance(item, dict)
            ],
            progress_signal=_progress_signal_from_record(record.get("progress_signal")),
            needs_follow_up=bool(record.get("needs_follow_up", False)),
            stop_hook_active=bool(record.get("stop_hook_active", False)),
            stop_hook_attempts=int(record.get("stop_hook_attempts", 0)),
        )
        return state

