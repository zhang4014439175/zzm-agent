from __future__ import annotations

import json
from pathlib import Path
from typing import Iterable

from zzm_agent.runtime.events import RUNTIME_EVENT_SCHEMA_VERSION, RuntimeEvent


class ExecutionJournal:
    """为所有入口提供单调序号、JSONL 持久化、查询和 Replay 的事实账本。"""

    def __init__(self, path: str | Path | None = None) -> None:
        self.path = Path(path) if path is not None else None
        self.events: list[RuntimeEvent] = []
        self._sequence = 0
        self._load()

    def append(self, event: RuntimeEvent) -> RuntimeEvent:
        """分配全局单调序号并追加事件；不接受未来未知 Schema。"""
        if event.schema_version > RUNTIME_EVENT_SCHEMA_VERSION:
            raise ValueError(f"Unsupported runtime event schema: {event.schema_version}")
        self._sequence += 1
        event.sequence = self._sequence
        self.events.append(event)
        if self.path is not None:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(event.to_record(), ensure_ascii=False, sort_keys=True))
                handle.write("\n")
        return event

    def query(
        self,
        *,
        session_id: str | None = None,
        turn_id: str | None = None,
        task_id: str | None = None,
        event_type: str | None = None,
        after_sequence: int = 0,
    ) -> list[RuntimeEvent]:
        """按状态关联和游标筛选事件，结果始终按事实顺序返回。"""
        return [
            event
            for event in self.events
            if event.sequence > after_sequence
            and (session_id is None or event.session_id == session_id)
            and (turn_id is None or event.turn_id == turn_id)
            and (task_id is None or event.task_id == task_id)
            and (event_type is None or event.event_type == event_type)
        ]

    def replay(self, *, after_sequence: int = 0) -> Iterable[RuntimeEvent]:
        """按原始序号回放事实，不重新执行模型或工具。"""
        yield from self.query(after_sequence=after_sequence)

    def _load(self) -> None:
        """从 JSONL 恢复完整记录；空行被忽略，损坏行明确抛错。"""
        if self.path is None or not self.path.exists():
            return
        with self.path.open(encoding="utf-8") as handle:
            for line in handle:
                if not line.strip():
                    continue
                event = RuntimeEvent.from_record(json.loads(line))
                if event.schema_version > RUNTIME_EVENT_SCHEMA_VERSION:
                    raise ValueError(f"Unsupported runtime event schema: {event.schema_version}")
                self.events.append(event)
                self._sequence = max(self._sequence, event.sequence)


class EventJsonlStore:
    """保留旧 append/read API，并使用 ExecutionJournal 的同一记录格式。"""

    def __init__(self, path: str | Path) -> None:
        self.journal = ExecutionJournal(path)

    @property
    def path(self) -> Path | None:
        """返回兼容路径属性。"""
        return self.journal.path

    def append(self, event: RuntimeEvent) -> None:
        """通过统一 Journal 追加事件并分配连续序号。"""
        self.journal.append(event)

    def read(self) -> list[RuntimeEvent]:
        """返回按序恢复的全部事件。"""
        return list(self.journal.events)
