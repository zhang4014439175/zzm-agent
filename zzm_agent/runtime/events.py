from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field, is_dataclass
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, Callable
from uuid import uuid4

from zzm_agent.security.content import redact_secrets

if TYPE_CHECKING:
    from zzm_agent.runtime.journal import ExecutionJournal


RUNTIME_EVENT_SCHEMA_VERSION = 1


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _json_ready(value: Any) -> Any:
    if hasattr(value, "to_record") and callable(value.to_record):
        return value.to_record()
    if is_dataclass(value):
        return asdict(value)
    try:
        json.dumps(value, ensure_ascii=False, sort_keys=True)
        return value
    except TypeError:
        return str(value)


@dataclass
class RuntimeEvent:
    """跨 CLI、JSONL 和 Replay 共用的版本化运行事实。"""

    event_type: str
    payload: dict[str, Any] = field(default_factory=dict)
    event_id: str = field(default_factory=lambda: f"event-{uuid4().hex[:12]}")
    timestamp: str = field(default_factory=_utc_now_iso)
    sequence: int = 0
    schema_version: int = RUNTIME_EVENT_SCHEMA_VERSION
    source: str = "runtime"
    session_id: str | None = None
    turn_id: str | None = None
    task_id: str | None = None
    state_scope: str | None = None
    state_id: str | None = None
    correlation_id: str | None = None
    parent_event_id: str | None = None

    def to_record(self) -> dict[str, Any]:
        """生成稳定 JSON 记录，供 Journal、CLI 与 Replay 共享。"""
        return {
            "schema_version": self.schema_version,
            "event_id": self.event_id,
            "event_type": self.event_type,
            "timestamp": self.timestamp,
            "sequence": self.sequence,
            "source": self.source,
            "session_id": self.session_id,
            "turn_id": self.turn_id,
            "task_id": self.task_id,
            "state_scope": self.state_scope,
            "state_id": self.state_id,
            "correlation_id": self.correlation_id,
            "parent_event_id": self.parent_event_id,
            "payload": redact_secrets(_json_ready(self.payload)),
        }

    @classmethod
    def from_record(cls, record: dict[str, Any]) -> "RuntimeEvent":
        """恢复新旧事件记录；旧记录缺少版本和关联字段时使用兼容默认值。"""
        return cls(
            event_id=str(record["event_id"]),
            event_type=str(record["event_type"]),
            timestamp=str(record.get("timestamp") or _utc_now_iso()),
            sequence=int(record.get("sequence") or 0),
            schema_version=int(record.get("schema_version") or 1),
            source=str(record.get("source") or "runtime"),
            session_id=record.get("session_id"),
            turn_id=record.get("turn_id"),
            task_id=record.get("task_id"),
            state_scope=record.get("state_scope"),
            state_id=record.get("state_id"),
            correlation_id=record.get("correlation_id"),
            parent_event_id=record.get("parent_event_id"),
            payload=dict(record.get("payload") or {}),
        )


EventSubscriber = Callable[[RuntimeEvent], None]


class EventBus:
    """发布 RuntimeEvent；观察者错误不能改变 Agent 执行结果。"""

    def __init__(self, journal: "ExecutionJournal | None" = None) -> None:
        self.events: list[RuntimeEvent] = []
        self.observer_errors: list[dict[str, str]] = []
        self._subscribers: dict[str | None, list[EventSubscriber]] = {}
        self._sequence = 0
        self.journal = journal

    def subscribe(
        self,
        subscriber: EventSubscriber,
        *,
        event_type: str | None = None,
    ) -> Callable[[], None]:
        """订阅全部或指定类型事件，并返回不会影响运行状态的取消订阅函数。"""
        self._subscribers.setdefault(event_type, []).append(subscriber)

        def unsubscribe() -> None:
            subscribers = self._subscribers.get(event_type, [])
            if subscriber in subscribers:
                subscribers.remove(subscriber)

        return unsubscribe

    def publish(
        self,
        event_type: str,
        payload: dict[str, Any] | None = None,
        *,
        source: str = "runtime",
        session_id: str | None = None,
        turn_id: str | None = None,
        task_id: str | None = None,
        state_scope: str | None = None,
        state_id: str | None = None,
        correlation_id: str | None = None,
        parent_event_id: str | None = None,
    ) -> RuntimeEvent:
        """创建有序事实，写入可选 Journal 后通知隔离的观察者。"""
        self._sequence += 1
        event = RuntimeEvent(
            event_type=event_type,
            payload=dict(payload or {}),
            sequence=self._sequence,
            source=source,
            session_id=session_id,
            turn_id=turn_id,
            task_id=task_id,
            state_scope=state_scope,
            state_id=state_id,
            correlation_id=correlation_id,
            parent_event_id=parent_event_id,
        )
        if self.journal is not None:
            self.journal.append(event)
        self.events.append(event)
        subscribers = self._subscribers.get(None, []) + self._subscribers.get(event_type, [])
        for subscriber in subscribers:
            try:
                subscriber(event)
            except Exception as exc:
                self.observer_errors.append(
                    {"event_id": event.event_id, "event_type": event.event_type, "error": str(exc)}
                )
        return event

    def to_records(self) -> list[dict[str, Any]]:
        """返回当前总线内按发布顺序排列的事件记录。"""
        return [event.to_record() for event in self.events]

    @classmethod
    def from_records(cls, records: list[dict[str, Any]] | None) -> "EventBus":
        """恢复事件总线，并从最大序号继续发布，防止序号回退。"""
        bus = cls()
        for record in records or []:
            event = RuntimeEvent.from_record(record)
            bus.events.append(event)
            bus._sequence = max(bus._sequence, event.sequence)
        return bus
