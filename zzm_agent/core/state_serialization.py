from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field, is_dataclass
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Generic, TypeVar

from zzm_agent.memory.io import StorageIO


STATE_SCHEMA_VERSION = 1


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
    return json.dumps(
        _json_ready(value),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )


def _checksum(payload: dict[str, Any]) -> str:
    digest = hashlib.sha256(_stable_json(payload).encode("utf-8")).hexdigest()
    return f"sha256:{digest}"


class StateSerializationError(RuntimeError):
    """Raised when a persisted state snapshot cannot be trusted."""


class RecoveryStatus(str, Enum):
    """Decision returned after validating whether a snapshot can be resumed."""

    RECOVERABLE = "recoverable"
    BLOCKED = "blocked"
    FAILED = "failed"


@dataclass(frozen=True)
class StateEnvelope:
    """Versioned, checksummed wrapper for one persisted runtime state."""

    state_type: str
    payload: dict[str, Any]
    schema_version: int = STATE_SCHEMA_VERSION
    created_at: str = field(default_factory=_utc_now_iso)
    metadata: dict[str, Any] = field(default_factory=dict)
    checksum: str = ""

    def to_record(self) -> dict[str, Any]:
        payload = _json_ready(self.payload)
        if not isinstance(payload, dict):
            payload = {"value": payload}
        checksum = self.checksum or _checksum(payload)
        return {
            "schema_version": self.schema_version,
            "state_type": self.state_type,
            "created_at": self.created_at,
            "metadata": dict(self.metadata),
            "payload": payload,
            "checksum": checksum,
        }

    @classmethod
    def from_record(cls, record: dict[str, Any]) -> "StateEnvelope":
        migrated = migrate_state_record(record)
        payload = migrated.get("payload")
        if not isinstance(payload, dict):
            raise StateSerializationError("State envelope payload must be an object.")
        expected = str(migrated.get("checksum") or "")
        actual = _checksum(payload)
        if expected and expected != actual:
            raise StateSerializationError(
                f"State checksum mismatch: expected {expected}, got {actual}."
            )
        return cls(
            schema_version=int(migrated.get("schema_version", STATE_SCHEMA_VERSION)),
            state_type=str(migrated.get("state_type", "")),
            created_at=str(migrated.get("created_at") or _utc_now_iso()),
            metadata=dict(migrated.get("metadata") or {}),
            payload=payload,
            checksum=expected or actual,
        )


def make_state_envelope(
    state: Any,
    *,
    state_type: str,
    metadata: dict[str, Any] | None = None,
) -> StateEnvelope:
    payload = _json_ready(state)
    if not isinstance(payload, dict):
        payload = {"value": payload}
    return StateEnvelope(
        state_type=state_type,
        payload=payload,
        metadata=dict(metadata or {}),
        checksum=_checksum(payload),
    )


def migrate_state_record(record: dict[str, Any]) -> dict[str, Any]:
    """Normalize legacy or older state records to the current envelope schema."""
    if not isinstance(record, dict):
        raise StateSerializationError("Persisted state must be a JSON object.")

    if "schema_version" not in record:
        state_type = str(record.get("state_type") or record.get("type") or "unknown")
        payload = dict(record.get("payload") or record.get("state") or record)
        migrated = {
            "schema_version": STATE_SCHEMA_VERSION,
            "state_type": state_type,
            "created_at": str(record.get("created_at") or _utc_now_iso()),
            "metadata": dict(record.get("metadata") or {"migrated_from": "legacy"}),
            "payload": payload,
        }
        migrated["checksum"] = _checksum(payload)
        return migrated

    version = int(record.get("schema_version", 0))
    if version > STATE_SCHEMA_VERSION:
        raise StateSerializationError(
            f"Unsupported state schema version: {version}."
        )
    if version == STATE_SCHEMA_VERSION:
        return dict(record)

    payload = dict(record.get("payload") or {})
    migrated = dict(record)
    migrated["schema_version"] = STATE_SCHEMA_VERSION
    migrated["payload"] = payload
    migrated["metadata"] = {
        **dict(record.get("metadata") or {}),
        "migrated_from_schema_version": version,
    }
    migrated["checksum"] = _checksum(payload)
    return migrated


T = TypeVar("T")


class StateSnapshotStore(Generic[T]):
    """Persist versioned state envelopes through StorageIO."""

    def __init__(self, path: str | Path, io: StorageIO | None = None) -> None:
        self.path = Path(path)
        self.io = io or StorageIO()

    def save(
        self,
        state: Any,
        *,
        state_type: str,
        metadata: dict[str, Any] | None = None,
    ) -> StateEnvelope:
        envelope = make_state_envelope(
            state,
            state_type=state_type,
            metadata=metadata,
        )
        self.io.write_json(self.path, envelope.to_record())
        return envelope

    def load_envelope(self) -> StateEnvelope | None:
        record = self.io.read_json(self.path, default=None)
        if record is None:
            return None
        return StateEnvelope.from_record(record)

    def load_state(
        self,
        restore: Callable[[dict[str, Any]], T],
    ) -> T | None:
        envelope = self.load_envelope()
        if envelope is None:
            return None
        return restore(envelope.payload)


@dataclass(frozen=True)
class RecoveryDecision:
    """Result of checking whether a persisted state can be safely resumed."""

    status: RecoveryStatus
    reason: str = ""
    details: list[str] = field(default_factory=list)

    @property
    def recoverable(self) -> bool:
        return self.status is RecoveryStatus.RECOVERABLE


@dataclass(frozen=True)
class RecoveryValidationContext:
    """External facts needed before accepting a restored snapshot."""

    workspace_path: str | Path | None = None
    artifact_paths: list[str | Path] = field(default_factory=list)
    memory_file_versions: dict[str, str] = field(default_factory=dict)
    current_file_versions: dict[str, str] = field(default_factory=dict)
    has_checkpoint: bool = False


class RecoveryValidator:
    """Classify restored snapshots as recoverable, blocked, or failed."""

    TERMINAL_TURN_STATUSES = {"completed", "blocked", "cancelled", "failed"}
    RUNNING_TURN_STATUSES = {"pending", "in_progress"}
    CHECKPOINTABLE_LOOP_PHASES = {
        "idle",
        "preparing",
        "processing_observations",
        "completed",
        "blocked",
        "cancelled",
        "failed",
    }

    def validate(
        self,
        envelope: StateEnvelope,
        *,
        context: RecoveryValidationContext | None = None,
    ) -> RecoveryDecision:
        context = context or RecoveryValidationContext()
        details: list[str] = []

        workspace = Path(context.workspace_path) if context.workspace_path else None
        if workspace is not None and not workspace.exists():
            return RecoveryDecision(
                RecoveryStatus.FAILED,
                reason="workspace_missing",
                details=[str(workspace)],
            )

        missing_artifacts = [
            str(path)
            for path in context.artifact_paths
            if not str(path).startswith("memory://") and not Path(path).exists()
        ]
        if missing_artifacts:
            return RecoveryDecision(
                RecoveryStatus.BLOCKED,
                reason="artifact_missing",
                details=missing_artifacts,
            )

        changed_files = [
            path
            for path, version in context.memory_file_versions.items()
            if context.current_file_versions.get(path) not in {None, version}
        ]
        if changed_files:
            return RecoveryDecision(
                RecoveryStatus.BLOCKED,
                reason="file_version_changed",
                details=changed_files,
            )

        active_turn = self._active_turn_payload(envelope.payload)
        if active_turn is not None:
            status = str(active_turn.get("status", ""))
            if status in self.RUNNING_TURN_STATUSES:
                loop = active_turn.get("loop") if isinstance(active_turn.get("loop"), dict) else {}
                phase = str(loop.get("phase", ""))
                if phase and phase not in self.CHECKPOINTABLE_LOOP_PHASES:
                    return RecoveryDecision(
                        RecoveryStatus.BLOCKED,
                        reason="running_state_requires_checkpoint",
                        details=[f"turn_status={status}", f"loop_phase={phase}"],
                    )
                if not context.has_checkpoint:
                    return RecoveryDecision(
                        RecoveryStatus.BLOCKED,
                        reason="running_state_requires_checkpoint",
                        details=[f"turn_status={status}"],
                    )
                details.append("running turn accepted because checkpoint is available")
            elif status and status not in self.TERMINAL_TURN_STATUSES:
                return RecoveryDecision(
                    RecoveryStatus.FAILED,
                    reason="unknown_turn_status",
                    details=[status],
                )

        return RecoveryDecision(
            RecoveryStatus.RECOVERABLE,
            reason="ok",
            details=details,
        )

    def _active_turn_payload(self, payload: dict[str, Any]) -> dict[str, Any] | None:
        if payload.get("turn_id") and payload.get("status"):
            return payload
        active_turn = payload.get("active_turn")
        if isinstance(active_turn, dict):
            return active_turn
        return None
