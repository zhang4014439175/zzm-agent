from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field, is_dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable
from uuid import uuid4


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


def _stable_json(value: Any) -> str:
    return json.dumps(_json_ready(value), ensure_ascii=False, sort_keys=True, default=str)


from zzm_agent.runtime.events import EventBus, RuntimeEvent
from zzm_agent.runtime.journal import EventJsonlStore


@dataclass
class ArtifactRecord:
    """Metadata for a stored large result, report, diff, log, or file."""

    artifact_id: str
    kind: str
    path: str
    mime_type: str = "text/plain"
    summary: str = ""
    size_bytes: int = 0
    checksum: str = ""
    session_id: str | None = None
    turn_id: str | None = None
    task_id: str | None = None
    created_at: str = field(default_factory=_utc_now_iso)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_record(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_record(cls, record: dict[str, Any]) -> "ArtifactRecord":
        return cls(
            artifact_id=str(record["artifact_id"]),
            kind=str(record.get("kind") or "artifact"),
            path=str(record["path"]),
            mime_type=str(record.get("mime_type") or "text/plain"),
            summary=str(record.get("summary") or ""),
            size_bytes=int(record.get("size_bytes") or 0),
            checksum=str(record.get("checksum") or ""),
            session_id=record.get("session_id"),
            turn_id=record.get("turn_id"),
            task_id=record.get("task_id"),
            created_at=str(record.get("created_at") or _utc_now_iso()),
            metadata=dict(record.get("metadata") or {}),
        )


class ArtifactStore:
    """Store full artifact content while exposing compact metadata to runtime state."""

    def __init__(self, root: str | Path | None = None) -> None:
        self.root = Path(root) if root is not None else None
        self.records: dict[str, ArtifactRecord] = {}
        self._memory_content: dict[str, bytes] = {}

    def save_text(
        self,
        content: str,
        *,
        kind: str,
        summary: str = "",
        mime_type: str = "text/plain",
        session_id: str | None = None,
        turn_id: str | None = None,
        task_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> ArtifactRecord:
        return self.save_bytes(
            content.encode("utf-8"),
            kind=kind,
            summary=summary,
            mime_type=mime_type,
            extension=".txt",
            session_id=session_id,
            turn_id=turn_id,
            task_id=task_id,
            metadata=metadata,
        )

    def save_json(
        self,
        content: Any,
        *,
        kind: str,
        summary: str = "",
        session_id: str | None = None,
        turn_id: str | None = None,
        task_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> ArtifactRecord:
        payload = json.dumps(_json_ready(content), ensure_ascii=False, indent=2, sort_keys=True)
        return self.save_bytes(
            payload.encode("utf-8"),
            kind=kind,
            summary=summary,
            mime_type="application/json",
            extension=".json",
            session_id=session_id,
            turn_id=turn_id,
            task_id=task_id,
            metadata=metadata,
        )

    def save_bytes(
        self,
        content: bytes,
        *,
        kind: str,
        summary: str = "",
        mime_type: str = "application/octet-stream",
        extension: str = ".bin",
        session_id: str | None = None,
        turn_id: str | None = None,
        task_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> ArtifactRecord:
        artifact_id = f"artifact-{uuid4().hex[:12]}"
        checksum = hashlib.sha256(content).hexdigest()
        path = self._write_content(artifact_id, content, extension=extension)
        record = ArtifactRecord(
            artifact_id=artifact_id,
            kind=kind,
            path=path,
            mime_type=mime_type,
            summary=summary,
            size_bytes=len(content),
            checksum=f"sha256:{checksum}",
            session_id=session_id,
            turn_id=turn_id,
            task_id=task_id,
            metadata=dict(metadata or {}),
        )
        self.records[artifact_id] = record
        return record

    def get(self, artifact_id: str) -> ArtifactRecord | None:
        return self.records.get(artifact_id)

    def list(
        self,
        *,
        session_id: str | None = None,
        turn_id: str | None = None,
        task_id: str | None = None,
    ) -> list[ArtifactRecord]:
        records = list(self.records.values())
        if session_id is not None:
            records = [record for record in records if record.session_id == session_id]
        if turn_id is not None:
            records = [record for record in records if record.turn_id == turn_id]
        if task_id is not None:
            records = [record for record in records if record.task_id == task_id]
        return records

    def read_bytes(self, artifact_id: str) -> bytes:
        record = self.records[artifact_id]
        if self.root is None:
            return self._memory_content[artifact_id]
        return Path(record.path).read_bytes()

    def read_text(self, artifact_id: str, *, encoding: str = "utf-8") -> str:
        return self.read_bytes(artifact_id).decode(encoding)

    def to_records(self) -> list[dict[str, Any]]:
        return [record.to_record() for record in self.records.values()]

    @classmethod
    def from_records(
        cls,
        records: list[dict[str, Any]] | None,
        *,
        root: str | Path | None = None,
    ) -> "ArtifactStore":
        store = cls(root=root)
        for record in records or []:
            restored = ArtifactRecord.from_record(record)
            store.records[restored.artifact_id] = restored
        return store

    def _write_content(self, artifact_id: str, content: bytes, *, extension: str) -> str:
        if self.root is None:
            self._memory_content[artifact_id] = content
            return f"memory://{artifact_id}"
        self.root.mkdir(parents=True, exist_ok=True)
        path = self.root / f"{artifact_id}{extension}"
        path.write_bytes(content)
        return str(path)


@dataclass
class CheckpointRecord:
    """One recoverable snapshot of conversation, turn, task, or working memory state."""

    checkpoint_id: str
    scope: str
    state: dict[str, Any]
    session_id: str | None = None
    turn_id: str | None = None
    task_id: str | None = None
    label: str = ""
    created_at: str = field(default_factory=_utc_now_iso)
    checksum: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_record(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_record(cls, record: dict[str, Any]) -> "CheckpointRecord":
        return cls(
            checkpoint_id=str(record["checkpoint_id"]),
            scope=str(record["scope"]),
            state=dict(record.get("state") or {}),
            session_id=record.get("session_id"),
            turn_id=record.get("turn_id"),
            task_id=record.get("task_id"),
            label=str(record.get("label") or ""),
            created_at=str(record.get("created_at") or _utc_now_iso()),
            checksum=str(record.get("checksum") or ""),
            metadata=dict(record.get("metadata") or {}),
        )


class CheckpointStore:
    """Checkpoint ledger for recovery and replay."""

    def __init__(self, root: str | Path | None = None) -> None:
        self.root = Path(root) if root is not None else None
        self.records: dict[str, CheckpointRecord] = {}

    def save(
        self,
        *,
        scope: str,
        state: Any,
        session_id: str | None = None,
        turn_id: str | None = None,
        task_id: str | None = None,
        label: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> CheckpointRecord:
        snapshot = _json_ready(state)
        if not isinstance(snapshot, dict):
            snapshot = {"value": snapshot}
        checksum = hashlib.sha256(_stable_json(snapshot).encode("utf-8")).hexdigest()
        record = CheckpointRecord(
            checkpoint_id=f"checkpoint-{uuid4().hex[:12]}",
            scope=scope,
            state=snapshot,
            session_id=session_id,
            turn_id=turn_id,
            task_id=task_id,
            label=label,
            checksum=f"sha256:{checksum}",
            metadata=dict(metadata or {}),
        )
        self.records[record.checkpoint_id] = record
        self._persist(record)
        return record

    def get(self, checkpoint_id: str) -> CheckpointRecord | None:
        return self.records.get(checkpoint_id)

    def list(
        self,
        *,
        scope: str | None = None,
        session_id: str | None = None,
        turn_id: str | None = None,
        task_id: str | None = None,
    ) -> list[CheckpointRecord]:
        records = list(self.records.values())
        if scope is not None:
            records = [record for record in records if record.scope == scope]
        if session_id is not None:
            records = [record for record in records if record.session_id == session_id]
        if turn_id is not None:
            records = [record for record in records if record.turn_id == turn_id]
        if task_id is not None:
            records = [record for record in records if record.task_id == task_id]
        return sorted(records, key=lambda record: record.created_at)

    def latest(
        self,
        *,
        scope: str | None = None,
        session_id: str | None = None,
        turn_id: str | None = None,
        task_id: str | None = None,
    ) -> CheckpointRecord | None:
        records = self.list(
            scope=scope,
            session_id=session_id,
            turn_id=turn_id,
            task_id=task_id,
        )
        return records[-1] if records else None

    def load_from_disk(self) -> None:
        if self.root is None or not self.root.exists():
            return
        for path in sorted(self.root.glob("checkpoint-*.json")):
            record = CheckpointRecord.from_record(json.loads(path.read_text(encoding="utf-8")))
            self.records[record.checkpoint_id] = record

    def to_records(self) -> list[dict[str, Any]]:
        return [record.to_record() for record in self.records.values()]

    @classmethod
    def from_records(
        cls,
        records: list[dict[str, Any]] | None,
        *,
        root: str | Path | None = None,
    ) -> "CheckpointStore":
        store = cls(root=root)
        for record in records or []:
            restored = CheckpointRecord.from_record(record)
            store.records[restored.checkpoint_id] = restored
        return store

    def _persist(self, record: CheckpointRecord) -> None:
        if self.root is None:
            return
        self.root.mkdir(parents=True, exist_ok=True)
        path = self.root / f"{record.checkpoint_id}.json"
        path.write_text(
            json.dumps(record.to_record(), ensure_ascii=False, indent=2, sort_keys=True),
            encoding="utf-8",
        )
