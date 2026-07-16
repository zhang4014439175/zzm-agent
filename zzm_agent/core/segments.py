from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from zzm_agent.core.runtime_state import TurnState, TurnStatus


@dataclass(frozen=True)
class SegmentResult:
    """表示一次有界 AgentLoop 执行段的结构化结果。

    一个 Segment 只是长任务的内部执行单元，不等同于整个用户任务。状态为
    ``yielded`` 时，检查点和剩余工作会交给 QueryEngine 自动开启下一段；只有
    completed、blocked、failed 或 cancelled 才是可向用户交付的终态。
    """

    status: TurnStatus
    reason: str
    reply: str = ""
    tool_iterations: int = 0
    tool_calls: int = 0
    checkpoint: dict[str, Any] = field(default_factory=dict)
    remaining_work_summary: str = ""
    turn: TurnState | None = None

    @property
    def should_continue(self) -> bool:
        """判断 QueryEngine 是否应在不询问用户的情况下自动续段。"""
        return self.status is TurnStatus.YIELDED

    def to_record(self) -> dict[str, Any]:
        """转换为适合事件日志和状态快照保存的字典。

        TurnState 本身可能包含大量运行时对象，因此这里只保存 ``turn_id``；
        检查点则复制后写出，避免调用方修改返回值时污染原始 SegmentResult。
        """
        return {
            "status": self.status.value,
            "reason": self.reason,
            "reply": self.reply,
            "tool_iterations": self.tool_iterations,
            "tool_calls": self.tool_calls,
            "checkpoint": dict(self.checkpoint),
            "remaining_work_summary": self.remaining_work_summary,
            "turn_id": self.turn.turn_id if self.turn is not None else None,
        }
