from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4


def utc_now() -> str:
    """返回 EffectRecord 使用的 UTC 时间。"""
    return datetime.now(timezone.utc).isoformat()


@dataclass
class EffectRecord:
    """一次工作区副作用及其授权、检查点和撤销事实。"""

    effect_id: str = field(default_factory=lambda: f"effect-{uuid4().hex[:12]}")
    kind: str = ""
    operation: str = ""
    target: str = ""
    status: str = "pending"
    authorized: bool = False
    reversible: bool = False
    checkpoint_id: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    created_at: str = field(default_factory=utc_now)
    completed_at: str | None = None
    reverted_at: str | None = None
    error: str | None = None

    def to_record(self) -> dict[str, Any]:
        """转换为可持久化字典。"""
        return asdict(self)

    @classmethod
    def from_record(cls, record: dict[str, Any]) -> "EffectRecord":
        """从兼容记录恢复副作用事实。"""
        values = {key: record.get(key) for key in cls.__dataclass_fields__}
        values["metadata"] = dict(values.get("metadata") or {})
        return cls(**values)


@dataclass(frozen=True)
class EffectUndoResult:
    """撤销副作用的可观察结果。"""

    effect: EffectRecord | None
    undone: bool
    message: str
