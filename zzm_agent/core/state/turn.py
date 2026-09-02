from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, ClassVar
from uuid import uuid4

from zzm_agent.core.observability import TokenUsage, UsageState
from zzm_agent.core.state_lifecycle import StateLifecyclePolicy, StateScope, get_state_policy
from zzm_agent.core.state.cancellation import CancellationToken
from zzm_agent.core.state.loop import LoopState
from zzm_agent.core.state.permission import PermissionState

class TurnStatus(str, Enum):
    """Lifecycle status for one user turn."""

    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    YIELDED = "yielded"
    BLOCKED = "blocked"
    CANCELLED = "cancelled"
    FAILED = "failed"
@dataclass(frozen=True)
class TurnTermination:
    """Persisted explanation of why control left one user turn."""

    status: TurnStatus
    reason: str
    provider_finish_reason: str | None = None
    recovery_attempts: int = 0
    occurred_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )

    def to_record(self) -> dict[str, Any]:
        return {
            "status": self.status.value,
            "reason": self.reason,
            "provider_finish_reason": self.provider_finish_reason,
            "recovery_attempts": self.recovery_attempts,
            "occurred_at": self.occurred_at,
        }

    @classmethod
    def from_record(cls, record: dict[str, Any]) -> "TurnTermination":
        return cls(
            status=TurnStatus(record.get("status", TurnStatus.FAILED.value)),
            reason=str(record.get("reason") or "unknown"),
            provider_finish_reason=record.get("provider_finish_reason"),
            recovery_attempts=max(0, int(record.get("recovery_attempts", 0) or 0)),
            occurred_at=str(
                record.get("occurred_at")
                or datetime.now(timezone.utc).isoformat()
            ),
        )

def _enum_value(value: Any) -> Any:
    if isinstance(value, Enum):
        return value.value
    return value


@dataclass
class TurnState:
    """保存一条用户消息从开始执行到终止的完整运行状态。

    状态包含用量、权限、取消、工具结果、Artifact、Provider 结束原因和统一终止
    记录。``checkpoint`` 只在安全让出时写入，供状态恢复和 QueryEngine 自动续段，
    不应被解释为用户任务已经完成。
    """

    user_input: str
    turn_id: str = field(default_factory=lambda: f"turn-{uuid4().hex[:8]}")
    status: TurnStatus | str = TurnStatus.PENDING
    usage: TokenUsage = field(default_factory=TokenUsage)
    usage_state: UsageState = field(default_factory=UsageState)
    discovered_skills: set[str] = field(default_factory=set)
    skill_discovery_state: dict[str, Any] = field(default_factory=dict)
    tool_exposure_state: dict[str, Any] = field(default_factory=dict)
    loaded_memory_paths: set[str] = field(default_factory=set)
    permission_requests: list[dict[str, Any]] = field(default_factory=list)
    permission_denials: list[dict[str, Any]] = field(default_factory=list)
    permissions: PermissionState = field(default_factory=PermissionState)
    cancellation_token: CancellationToken | None = None
    artifacts: list[dict[str, Any]] = field(default_factory=list)
    tool_results: list[dict[str, Any]] = field(default_factory=list)
    loop: LoopState | None = None
    final_response: str | None = None
    error: str | None = None
    provider_finish_reason: str | None = None
    provider_finish_reason_history: list[str] = field(default_factory=list)
    termination: TurnTermination | None = None
    checkpoint: dict[str, Any] = field(default_factory=dict)

    scope: ClassVar[StateScope] = StateScope.TURN

    @classmethod
    def policy(cls) -> StateLifecyclePolicy:
        return get_state_policy(cls.scope)

    def start(self) -> None:
        self.status = TurnStatus.IN_PROGRESS
        self.usage_state.start_turn(self.turn_id)

    def start_loop(self) -> LoopState:
        self.loop = LoopState()
        self.loop.prepare_next_turn()
        self.status = TurnStatus.IN_PROGRESS
        return self.loop

    def add_usage(self, usage: TokenUsage) -> None:
        self.usage.add(usage)
        self.usage_state.turn.add(usage)

    def record_provider_finish_reason(self, reason: str | None) -> None:
        if reason is None:
            return
        normalized = str(reason).strip()
        if not normalized:
            return
        self.provider_finish_reason = normalized
        self.provider_finish_reason_history.append(normalized)

    def _terminate(
        self,
        status: TurnStatus,
        reason: str,
        *,
        recovery_attempts: int = 0,
    ) -> None:
        self.status = status
        self.termination = TurnTermination(
            status=status,
            reason=reason,
            provider_finish_reason=self.provider_finish_reason,
            recovery_attempts=recovery_attempts,
        )

    def complete(
        self,
        final_response: str,
        usage: TokenUsage | None = None,
        *,
        reason: str = "model_completed",
    ) -> None:
        if usage is not None:
            self.usage = usage.copy()
            self.usage_state.turn = usage.copy()
        self.final_response = final_response
        self.error = None
        self._terminate(TurnStatus.COMPLETED, reason)
        if self.loop is not None:
            self.loop.mark_completed(reason)

    def block(
        self,
        final_response: str,
        reason: str = "blocked",
        *,
        recovery_attempts: int = 0,
    ) -> None:
        self.final_response = final_response
        self.error = reason
        self._terminate(
            TurnStatus.BLOCKED,
            reason,
            recovery_attempts=recovery_attempts,
        )
        if self.loop is not None:
            self.loop.mark_blocked(reason)

    def yield_control(self, reason: str = "yielded") -> None:
        self.error = None
        self._terminate(TurnStatus.YIELDED, reason)
        if self.loop is not None:
            self.loop.mark_yielded(reason)

    def fail(self, error: str) -> None:
        self.error = error
        self._terminate(TurnStatus.FAILED, error)
        if self.loop is not None:
            self.loop.mark_failed(error)

    def cancel(self, reason: str = "cancelled") -> None:
        self.error = reason
        self._terminate(TurnStatus.CANCELLED, reason)
        if self.loop is not None:
            self.loop.mark_cancelled(reason)

    def to_record(self) -> dict[str, Any]:
        """把 Turn 的运行事实转换为 JSON 兼容快照。

        该记录包含 Segment 检查点，因此进程重启后仍能知道上段为何让出、已经
        产生哪些 Artifact 以及剩余工作。集合会转换为稳定列表，嵌套状态通过各自
        的 ``to_record`` 保存；本方法不改变当前 Turn。
        """
        return {
            "turn_id": self.turn_id,
            "user_input": self.user_input,
            "status": _enum_value(self.status),
            "usage": self.usage.to_record(),
            "usage_state": self.usage_state.to_record(),
            "discovered_skills": sorted(self.discovered_skills),
            "skill_discovery_state": dict(self.skill_discovery_state),
            "tool_exposure_state": dict(self.tool_exposure_state),
            "loaded_memory_paths": sorted(self.loaded_memory_paths),
            "permission_requests": list(self.permission_requests),
            "permission_denials": list(self.permission_denials),
            "permissions": self.permissions.to_record(),
            "cancellation_token": (
                self.cancellation_token.to_record()
                if self.cancellation_token is not None
                else None
            ),
            "artifacts": list(self.artifacts),
            "tool_results": list(self.tool_results),
            "loop": self.loop.to_record() if self.loop is not None else None,
            "final_response": self.final_response,
            "error": self.error,
            "provider_finish_reason": self.provider_finish_reason,
            "provider_finish_reason_history": list(
                self.provider_finish_reason_history
            ),
            "termination": (
                self.termination.to_record() if self.termination is not None else None
            ),
            "checkpoint": dict(self.checkpoint),
        }

    @classmethod
    def from_record(cls, record: dict[str, Any] | None) -> "TurnState":
        """从持久化记录恢复 TurnState，并兼容没有检查点的旧快照。

        空记录会返回可安全初始化的空 Turn；旧版本缺少 ``checkpoint`` 时使用空
        字典。恢复只重建状态，不会重新执行模型、工具或生命周期 Hook。
        """
        if not record:
            return cls(user_input="")
        state = cls(
            turn_id=str(record.get("turn_id") or f"turn-{uuid4().hex[:8]}"),
            user_input=str(record.get("user_input", "")),
            status=TurnStatus(record.get("status", TurnStatus.PENDING.value)),
            usage=TokenUsage.from_record(record.get("usage")),
            usage_state=UsageState.from_record(record.get("usage_state")),
            discovered_skills=set(record.get("discovered_skills", [])),
            skill_discovery_state=dict(record.get("skill_discovery_state", {})),
            tool_exposure_state=dict(record.get("tool_exposure_state", {})),
            loaded_memory_paths=set(record.get("loaded_memory_paths", [])),
            permission_requests=[
                dict(item)
                for item in record.get("permission_requests", [])
                if isinstance(item, dict)
            ],
            permission_denials=[
                dict(item)
                for item in record.get("permission_denials", [])
                if isinstance(item, dict)
            ],
            permissions=PermissionState.from_record(record.get("permissions")),
            cancellation_token=(
                CancellationToken.from_record(record["cancellation_token"])
                if isinstance(record.get("cancellation_token"), dict)
                else None
            ),
            artifacts=[
                dict(item)
                for item in record.get("artifacts", [])
                if isinstance(item, dict)
            ],
            tool_results=[
                dict(item)
                for item in record.get("tool_results", [])
                if isinstance(item, dict)
            ],
            loop=(
                LoopState.from_record(record["loop"])
                if isinstance(record.get("loop"), dict)
                else None
            ),
            final_response=record.get("final_response"),
            error=record.get("error"),
            provider_finish_reason=record.get("provider_finish_reason"),
            provider_finish_reason_history=[
                str(item)
                for item in record.get("provider_finish_reason_history", [])
                if item is not None
            ],
            termination=(
                TurnTermination.from_record(record["termination"])
                if isinstance(record.get("termination"), dict)
                else None
            ),
            checkpoint=dict(record.get("checkpoint") or {}),
        )
        return state
