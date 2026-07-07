from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Callable, ClassVar
from uuid import uuid4

from zzm_agent.core.observability import TokenUsage, UsageState
from zzm_agent.core.progress_monitor import ProgressSignal, ToolObservation
from zzm_agent.core.runtime_records import ArtifactStore, CheckpointStore, EventBus
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


class CancellationError(RuntimeError):
    """Raised when a runtime cancellation checkpoint is reached."""

    def __init__(self, token: "CancellationToken") -> None:
        self.token = token
        super().__init__(token.reason or "cancelled")


@dataclass
class CancellationToken:
    """One node in the hierarchical runtime cancellation tree."""

    token_id: str
    scope: str
    parent_id: str | None = None
    reason: str | None = None
    cancelled_at: str | None = None
    children: dict[str, "CancellationToken"] = field(default_factory=dict)
    _callbacks: list[Callable[["CancellationToken"], None]] = field(
        default_factory=list,
        repr=False,
        compare=False,
    )

    @property
    def is_cancelled(self) -> bool:
        return self.cancelled_at is not None

    def child(self, token_id: str, *, scope: str) -> "CancellationToken":
        if token_id in self.children:
            return self.children[token_id]
        token = CancellationToken(token_id=token_id, scope=scope, parent_id=self.token_id)
        self.children[token_id] = token
        if self.is_cancelled:
            token.cancel(self.reason or "parent_cancelled", cancelled_at=self.cancelled_at)
        return token

    def register_callback(
        self,
        callback: Callable[["CancellationToken"], None],
    ) -> Callable[[], None]:
        if self.is_cancelled:
            callback(self)
            return lambda: None
        self._callbacks.append(callback)

        def unregister() -> None:
            if callback in self._callbacks:
                self._callbacks.remove(callback)

        return unregister

    def cancel(self, reason: str = "cancelled", *, cancelled_at: str | None = None) -> None:
        if self.is_cancelled:
            return
        self.reason = reason
        self.cancelled_at = cancelled_at or _utc_now_iso()
        callbacks = list(self._callbacks)
        self._callbacks.clear()
        for callback in callbacks:
            callback(self)
        for child in self.children.values():
            child.cancel(reason, cancelled_at=self.cancelled_at)

    def raise_if_cancelled(self) -> None:
        if self.is_cancelled:
            raise CancellationError(self)

    def to_record(self) -> dict[str, Any]:
        return {
            "token_id": self.token_id,
            "scope": self.scope,
            "parent_id": self.parent_id,
            "reason": self.reason,
            "cancelled_at": self.cancelled_at,
            "children": {
                token_id: token.to_record()
                for token_id, token in self.children.items()
            },
        }

    @classmethod
    def from_record(cls, record: dict[str, Any]) -> "CancellationToken":
        token = cls(
            token_id=str(record["token_id"]),
            scope=str(record["scope"]),
            parent_id=record.get("parent_id"),
            reason=record.get("reason"),
            cancelled_at=record.get("cancelled_at"),
        )
        token.children = {
            str(token_id): cls.from_record(child)
            for token_id, child in record.get("children", {}).items()
            if isinstance(child, dict)
        }
        return token


@dataclass
class CancellationController:
    """Creates and owns session, turn, task, and child cancellation tokens."""

    session_id: str
    session_token: CancellationToken = field(init=False)
    active_turn_token_id: str | None = None
    active_task_token_id: str | None = None

    def __post_init__(self) -> None:
        self.session_token = CancellationToken(
            token_id=f"session:{self.session_id}",
            scope="session",
        )

    @property
    def active_turn_token(self) -> CancellationToken | None:
        if self.active_turn_token_id is None:
            return None
        return self.session_token.children.get(self.active_turn_token_id)

    @property
    def active_task_token(self) -> CancellationToken | None:
        turn = self.active_turn_token
        if turn is None or self.active_task_token_id is None:
            return None
        return turn.children.get(self.active_task_token_id)

    def start_turn(self, turn_id: str) -> CancellationToken:
        token_id = f"turn:{turn_id}"
        token = self.session_token.child(token_id, scope="turn")
        self.active_turn_token_id = token_id
        self.active_task_token_id = None
        return token

    def start_task(
        self,
        task_id: str,
        *,
        turn_token: CancellationToken | None = None,
    ) -> CancellationToken:
        parent = turn_token or self.active_turn_token
        if parent is None:
            raise RuntimeError("Cannot start a task without an active turn token.")
        token_id = f"task:{task_id}"
        token = parent.child(token_id, scope="task")
        self.active_task_token_id = token_id
        return token

    def create_child(
        self,
        child_id: str,
        *,
        parent: CancellationToken | None = None,
        scope: str = "child",
    ) -> CancellationToken:
        parent_token = (
            parent
            or self.active_task_token
            or self.active_turn_token
            or self.session_token
        )
        return parent_token.child(f"{scope}:{child_id}", scope=scope)

    def cancel_session(self, reason: str = "cancelled") -> None:
        self.session_token.cancel(reason)

    def cancel_turn(self, reason: str = "cancelled") -> None:
        token = self.active_turn_token
        if token is None:
            raise RuntimeError("No active turn token to cancel.")
        token.cancel(reason)

    def cancel_task(self, reason: str = "cancelled") -> None:
        token = self.active_task_token
        if token is None:
            raise RuntimeError("No active task token to cancel.")
        token.cancel(reason)

    def raise_if_cancelled(self) -> None:
        token = self.active_task_token or self.active_turn_token or self.session_token
        token.raise_if_cancelled()

    def finish_turn(self) -> None:
        self.active_turn_token_id = None
        self.active_task_token_id = None

    def to_record(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "session_token": self.session_token.to_record(),
            "active_turn_token_id": self.active_turn_token_id,
            "active_task_token_id": self.active_task_token_id,
        }

    @classmethod
    def from_record(cls, record: dict[str, Any] | None) -> "CancellationController":
        if not record:
            return cls(session_id="")
        controller = cls(session_id=str(record.get("session_id", "")))
        session_token = record.get("session_token")
        if isinstance(session_token, dict):
            controller.session_token = CancellationToken.from_record(session_token)
        controller.active_turn_token_id = record.get("active_turn_token_id")
        controller.active_task_token_id = record.get("active_task_token_id")
        return controller


class PermissionStatus(str, Enum):
    """Lifecycle status for a permission request or decision."""

    PENDING = "pending"
    APPROVED_ONCE = "approved_once"
    APPROVED_SESSION = "approved_session"
    APPROVED_TASK = "approved_task"
    DENIED = "denied"
    EXPIRED = "expired"
    ORPHANED = "orphaned"
    CANCELLED = "cancelled"


class PermissionScope(str, Enum):
    """Scope for an approval decision."""

    ONCE = "once"
    SESSION = "session"
    TASK = "task"


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _coerce_permission_scope(scope: PermissionScope | str) -> PermissionScope:
    if isinstance(scope, PermissionScope):
        return scope
    return PermissionScope(str(scope))


def _permission_status_for_scope(scope: PermissionScope | str) -> PermissionStatus:
    normalized = _coerce_permission_scope(scope)
    if normalized is PermissionScope.SESSION:
        return PermissionStatus.APPROVED_SESSION
    if normalized is PermissionScope.TASK:
        return PermissionStatus.APPROVED_TASK
    return PermissionStatus.APPROVED_ONCE


def summarize_permission_arguments(arguments: Any) -> str:
    """Return a stable, compact argument summary for permission records."""
    try:
        normalized = json.dumps(arguments, ensure_ascii=False, sort_keys=True, default=str)
    except TypeError:
        normalized = str(arguments)
    digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:12]
    preview = normalized if len(normalized) <= 160 else f"{normalized[:157]}..."
    return f"sha256:{digest} {preview}"


def _permission_signature(tool_name: str, arguments_summary: str) -> str:
    return f"{tool_name}:{arguments_summary}"


@dataclass
class PermissionRequest:
    """One pending or historically handled permission request."""

    request_id: str
    tool_name: str
    arguments_summary: str
    risk_level: str
    scope: PermissionScope | str = PermissionScope.ONCE
    status: PermissionStatus | str = PermissionStatus.PENDING
    reason: str = ""
    tool_call_id: str | None = None
    turn_id: str | None = None
    task_id: str | None = None
    created_at: str = field(default_factory=_utc_now_iso)
    updated_at: str | None = None
    expires_at: str | None = None

    def to_record(self) -> dict[str, Any]:
        return {
            "request_id": self.request_id,
            "tool_name": self.tool_name,
            "arguments_summary": self.arguments_summary,
            "risk_level": self.risk_level,
            "scope": str(self.scope.value if isinstance(self.scope, PermissionScope) else self.scope),
            "status": str(
                self.status.value if isinstance(self.status, PermissionStatus) else self.status
            ),
            "reason": self.reason,
            "tool_call_id": self.tool_call_id,
            "turn_id": self.turn_id,
            "task_id": self.task_id,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "expires_at": self.expires_at,
        }

    @classmethod
    def from_record(cls, record: dict[str, Any]) -> "PermissionRequest":
        return cls(
            request_id=str(record["request_id"]),
            tool_name=str(record["tool_name"]),
            arguments_summary=str(record["arguments_summary"]),
            risk_level=str(record.get("risk_level", "unknown")),
            scope=PermissionScope(record.get("scope", PermissionScope.ONCE.value)),
            status=PermissionStatus(record.get("status", PermissionStatus.PENDING.value)),
            reason=str(record.get("reason", "")),
            tool_call_id=record.get("tool_call_id"),
            turn_id=record.get("turn_id"),
            task_id=record.get("task_id"),
            created_at=str(record.get("created_at") or _utc_now_iso()),
            updated_at=record.get("updated_at"),
            expires_at=record.get("expires_at"),
        )


@dataclass
class PermissionDecision:
    """A durable permission decision derived from a request."""

    decision_id: str
    request_id: str
    tool_name: str
    arguments_summary: str
    risk_level: str
    status: PermissionStatus | str
    scope: PermissionScope | str
    reason: str = ""
    tool_call_id: str | None = None
    turn_id: str | None = None
    task_id: str | None = None
    decided_at: str = field(default_factory=_utc_now_iso)
    expires_at: str | None = None

    def to_record(self) -> dict[str, Any]:
        return {
            "decision_id": self.decision_id,
            "request_id": self.request_id,
            "tool_name": self.tool_name,
            "arguments_summary": self.arguments_summary,
            "risk_level": self.risk_level,
            "status": str(
                self.status.value if isinstance(self.status, PermissionStatus) else self.status
            ),
            "scope": str(self.scope.value if isinstance(self.scope, PermissionScope) else self.scope),
            "reason": self.reason,
            "tool_call_id": self.tool_call_id,
            "turn_id": self.turn_id,
            "task_id": self.task_id,
            "decided_at": self.decided_at,
            "expires_at": self.expires_at,
        }

    @classmethod
    def from_record(cls, record: dict[str, Any]) -> "PermissionDecision":
        return cls(
            decision_id=str(record["decision_id"]),
            request_id=str(record["request_id"]),
            tool_name=str(record["tool_name"]),
            arguments_summary=str(record["arguments_summary"]),
            risk_level=str(record.get("risk_level", "unknown")),
            status=PermissionStatus(record.get("status", PermissionStatus.PENDING.value)),
            scope=PermissionScope(record.get("scope", PermissionScope.ONCE.value)),
            reason=str(record.get("reason", "")),
            tool_call_id=record.get("tool_call_id"),
            turn_id=record.get("turn_id"),
            task_id=record.get("task_id"),
            decided_at=str(record.get("decided_at") or _utc_now_iso()),
            expires_at=record.get("expires_at"),
        )


@dataclass
class PermissionState:
    """Runtime permission ledger for one conversation or task."""

    pending_requests: dict[str, PermissionRequest] = field(default_factory=dict)
    decisions: list[PermissionDecision] = field(default_factory=list)
    denials: list[PermissionDecision] = field(default_factory=list)
    session_grants: dict[str, PermissionDecision] = field(default_factory=dict)
    task_grants: dict[str, PermissionDecision] = field(default_factory=dict)
    orphaned_requests: list[PermissionRequest] = field(default_factory=list)
    has_handled_orphaned_permission: bool = False

    def request_permission(
        self,
        *,
        tool_name: str,
        arguments: Any,
        risk_level: str,
        tool_call_id: str | None = None,
        scope: PermissionScope | str = PermissionScope.ONCE,
        reason: str = "",
        turn_id: str | None = None,
        task_id: str | None = None,
        expires_at: str | None = None,
    ) -> PermissionRequest:
        request = PermissionRequest(
            request_id=f"perm-{uuid4().hex[:12]}",
            tool_name=tool_name,
            arguments_summary=summarize_permission_arguments(arguments),
            risk_level=risk_level,
            scope=_coerce_permission_scope(scope),
            reason=reason,
            tool_call_id=tool_call_id,
            turn_id=turn_id,
            task_id=task_id,
            expires_at=expires_at,
        )
        self.pending_requests[request.request_id] = request
        return request

    def approve_request(
        self,
        request_id: str,
        *,
        scope: PermissionScope | str = PermissionScope.ONCE,
        reason: str = "",
        expires_at: str | None = None,
    ) -> PermissionDecision:
        return self._decide(
            request_id,
            status=_permission_status_for_scope(scope),
            scope=scope,
            reason=reason,
            expires_at=expires_at,
        )

    def deny_request(self, request_id: str, *, reason: str = "") -> PermissionDecision:
        decision = self._decide(
            request_id,
            status=PermissionStatus.DENIED,
            scope=PermissionScope.ONCE,
            reason=reason,
        )
        self.denials.append(decision)
        return decision

    def expire_request(self, request_id: str, *, reason: str = "expired") -> PermissionDecision:
        return self._decide(
            request_id,
            status=PermissionStatus.EXPIRED,
            scope=PermissionScope.ONCE,
            reason=reason,
        )

    def cancel_request(self, request_id: str, *, reason: str = "cancelled") -> PermissionDecision:
        return self._decide(
            request_id,
            status=PermissionStatus.CANCELLED,
            scope=PermissionScope.ONCE,
            reason=reason,
        )

    def orphan_request(self, request_id: str, *, reason: str = "orphaned") -> PermissionRequest:
        request = self.pending_requests.pop(request_id)
        request.status = PermissionStatus.ORPHANED
        request.reason = reason
        request.updated_at = _utc_now_iso()
        self.orphaned_requests.append(request)
        return request

    def handle_orphaned_permissions(self) -> list[PermissionRequest]:
        self.has_handled_orphaned_permission = True
        orphaned = list(self.pending_requests.values())
        for request in orphaned:
            self.orphan_request(request.request_id)
        return orphaned

    def find_active_grant(
        self,
        *,
        tool_name: str,
        arguments: Any,
        task_id: str | None = None,
        now: str | None = None,
    ) -> PermissionDecision | None:
        arguments_summary = summarize_permission_arguments(arguments)
        signature = _permission_signature(tool_name, arguments_summary)
        current_time = now or _utc_now_iso()

        task_grant = self.task_grants.get(signature)
        if (
            task_grant is not None
            and (task_id is None or task_grant.task_id == task_id)
            and not self._is_expired(task_grant, current_time)
        ):
            return task_grant

        session_grant = self.session_grants.get(signature)
        if session_grant is not None and not self._is_expired(session_grant, current_time):
            return session_grant
        return None

    def to_record(self) -> dict[str, Any]:
        return {
            "pending_requests": {
                request_id: request.to_record()
                for request_id, request in self.pending_requests.items()
            },
            "decisions": [decision.to_record() for decision in self.decisions],
            "denials": [decision.to_record() for decision in self.denials],
            "session_grants": {
                signature: decision.to_record()
                for signature, decision in self.session_grants.items()
            },
            "task_grants": {
                signature: decision.to_record()
                for signature, decision in self.task_grants.items()
            },
            "orphaned_requests": [
                request.to_record() for request in self.orphaned_requests
            ],
            "has_handled_orphaned_permission": self.has_handled_orphaned_permission,
        }

    @classmethod
    def from_record(cls, record: dict[str, Any] | None) -> "PermissionState":
        if not record:
            return cls()
        return cls(
            pending_requests={
                str(request_id): PermissionRequest.from_record(request_record)
                for request_id, request_record in record.get("pending_requests", {}).items()
            },
            decisions=[
                PermissionDecision.from_record(decision)
                for decision in record.get("decisions", [])
            ],
            denials=[
                PermissionDecision.from_record(decision)
                for decision in record.get("denials", [])
            ],
            session_grants={
                str(signature): PermissionDecision.from_record(decision)
                for signature, decision in record.get("session_grants", {}).items()
            },
            task_grants={
                str(signature): PermissionDecision.from_record(decision)
                for signature, decision in record.get("task_grants", {}).items()
            },
            orphaned_requests=[
                PermissionRequest.from_record(request)
                for request in record.get("orphaned_requests", [])
            ],
            has_handled_orphaned_permission=bool(
                record.get("has_handled_orphaned_permission", False)
            ),
        )

    def _decide(
        self,
        request_id: str,
        *,
        status: PermissionStatus,
        scope: PermissionScope | str,
        reason: str,
        expires_at: str | None = None,
    ) -> PermissionDecision:
        request = self.pending_requests.pop(request_id)
        normalized_scope = _coerce_permission_scope(scope)
        request.status = status
        request.scope = normalized_scope
        request.reason = reason
        request.updated_at = _utc_now_iso()
        if expires_at is not None:
            request.expires_at = expires_at
        decision = PermissionDecision(
            decision_id=f"decision-{uuid4().hex[:12]}",
            request_id=request.request_id,
            tool_name=request.tool_name,
            arguments_summary=request.arguments_summary,
            risk_level=request.risk_level,
            status=status,
            scope=normalized_scope,
            reason=reason,
            tool_call_id=request.tool_call_id,
            turn_id=request.turn_id,
            task_id=request.task_id,
            expires_at=request.expires_at,
        )
        self.decisions.append(decision)
        signature = _permission_signature(decision.tool_name, decision.arguments_summary)
        if status is PermissionStatus.APPROVED_SESSION:
            self.session_grants[signature] = decision
        elif status is PermissionStatus.APPROVED_TASK:
            self.task_grants[signature] = decision
        return decision

    def _is_expired(self, decision: PermissionDecision, now: str) -> bool:
        return bool(decision.expires_at and decision.expires_at <= now)


def _content_hash(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8", errors="replace")).hexdigest()


def _stable_id(prefix: str, payload: Any) -> str:
    serialized = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)
    digest = hashlib.sha256(serialized.encode("utf-8")).hexdigest()[:16]
    return f"{prefix}-{digest}"


@dataclass
class FileReadRange:
    """One range read from a cached file."""

    start_line: int
    end_line: int
    read_at: str = field(default_factory=_utc_now_iso)

    def to_record(self) -> dict[str, Any]:
        return {
            "start_line": self.start_line,
            "end_line": self.end_line,
            "read_at": self.read_at,
        }

    @classmethod
    def from_record(cls, record: dict[str, Any]) -> "FileReadRange":
        return cls(
            start_line=int(record.get("start_line", 1)),
            end_line=int(record.get("end_line", 1)),
            read_at=str(record.get("read_at") or _utc_now_iso()),
        )


@dataclass
class FileState:
    """Cached metadata and optional content for one workspace file."""

    normalized_path: str
    content_hash: str
    size_bytes: int
    mtime_ns: int
    encoding: str = "utf-8"
    line_count: int = 0
    content: str | None = None
    content_reference: str | None = None
    read_ranges: list[FileReadRange] = field(default_factory=list)
    summary: str = ""
    last_read_at: str | None = None
    agent_last_modified_at: str | None = None
    version: int = 1

    def matches_file(self, *, size_bytes: int, mtime_ns: int) -> bool:
        return self.size_bytes == size_bytes and self.mtime_ns == mtime_ns

    def has_range(self, start_line: int, end_line: int) -> bool:
        return any(
            item.start_line <= start_line and item.end_line >= end_line
            for item in self.read_ranges
        )

    def record_range(self, start_line: int, end_line: int) -> None:
        self.read_ranges.append(FileReadRange(start_line=start_line, end_line=end_line))
        self.last_read_at = self.read_ranges[-1].read_at

    def to_record(self) -> dict[str, Any]:
        return {
            "normalized_path": self.normalized_path,
            "content_hash": self.content_hash,
            "size_bytes": self.size_bytes,
            "mtime_ns": self.mtime_ns,
            "encoding": self.encoding,
            "line_count": self.line_count,
            "content": self.content,
            "content_reference": self.content_reference,
            "read_ranges": [item.to_record() for item in self.read_ranges],
            "summary": self.summary,
            "last_read_at": self.last_read_at,
            "agent_last_modified_at": self.agent_last_modified_at,
            "version": self.version,
        }

    @classmethod
    def from_record(cls, record: dict[str, Any]) -> "FileState":
        return cls(
            normalized_path=str(record["normalized_path"]),
            content_hash=str(record.get("content_hash", "")),
            size_bytes=int(record.get("size_bytes", 0)),
            mtime_ns=int(record.get("mtime_ns", 0)),
            encoding=str(record.get("encoding", "utf-8")),
            line_count=int(record.get("line_count", 0)),
            content=record.get("content"),
            content_reference=record.get("content_reference"),
            read_ranges=[
                FileReadRange.from_record(item)
                for item in record.get("read_ranges", [])
                if isinstance(item, dict)
            ],
            summary=str(record.get("summary", "")),
            last_read_at=record.get("last_read_at"),
            agent_last_modified_at=record.get("agent_last_modified_at"),
            version=int(record.get("version", 1)),
        )


@dataclass
class FileStateCache:
    """Runtime cache for file reads and agent file writes."""

    files: dict[str, FileState] = field(default_factory=dict)
    invalidated_paths: set[str] = field(default_factory=set)

    def get_valid(
        self,
        *,
        normalized_path: str,
        size_bytes: int,
        mtime_ns: int,
    ) -> FileState | None:
        state = self.files.get(normalized_path)
        if state is None:
            return None
        if state.matches_file(size_bytes=size_bytes, mtime_ns=mtime_ns):
            return state
        self.invalidate(normalized_path)
        return None

    def record_read(
        self,
        *,
        normalized_path: str,
        content: str,
        size_bytes: int,
        mtime_ns: int,
        start_line: int,
        end_line: int,
        encoding: str = "utf-8",
    ) -> FileState:
        previous = self.files.get(normalized_path)
        version = previous.version + 1 if previous else 1
        lines = content.splitlines()
        summary = self._summarize_content(content)
        state = FileState(
            normalized_path=normalized_path,
            content_hash=_content_hash(content),
            size_bytes=size_bytes,
            mtime_ns=mtime_ns,
            encoding=encoding,
            line_count=len(lines),
            content=content,
            read_ranges=[],
            summary=summary,
            version=version,
        )
        state.record_range(start_line=start_line, end_line=end_line)
        self.files[normalized_path] = state
        self.invalidated_paths.discard(normalized_path)
        return state

    def update_after_write(
        self,
        *,
        normalized_path: str,
        content: str,
        size_bytes: int,
        mtime_ns: int,
        encoding: str = "utf-8",
    ) -> FileState:
        state = self.record_read(
            normalized_path=normalized_path,
            content=content,
            size_bytes=size_bytes,
            mtime_ns=mtime_ns,
            start_line=1,
            end_line=max(1, len(content.splitlines())),
            encoding=encoding,
        )
        state.agent_last_modified_at = _utc_now_iso()
        return state

    def invalidate(self, normalized_path: str) -> None:
        self.files.pop(normalized_path, None)
        self.invalidated_paths.add(normalized_path)

    def to_record(self) -> dict[str, Any]:
        return {
            "files": {
                path: state.to_record()
                for path, state in self.files.items()
            },
            "invalidated_paths": sorted(self.invalidated_paths),
        }

    @classmethod
    def from_record(cls, record: dict[str, Any] | None) -> "FileStateCache":
        if not record:
            return cls()
        return cls(
            files={
                str(path): FileState.from_record(state)
                for path, state in record.get("files", {}).items()
                if isinstance(state, dict)
            },
            invalidated_paths=set(record.get("invalidated_paths", [])),
        )

    def _summarize_content(self, content: str, limit: int = 240) -> str:
        text = " ".join(content.split())
        if len(text) <= limit:
            return text
        return text[: limit - 3] + "..."


@dataclass
class MemorySourceRecord:
    """One memory source injected or loaded into a model turn."""

    source_id: str
    source_type: str
    content: str
    path: str | None = None
    version: str | None = None
    loaded_at: str = field(default_factory=_utc_now_iso)

    def to_record(self) -> dict[str, Any]:
        return {
            "source_id": self.source_id,
            "source_type": self.source_type,
            "content": self.content,
            "path": self.path,
            "version": self.version,
            "loaded_at": self.loaded_at,
        }

    @classmethod
    def from_record(cls, record: dict[str, Any]) -> "MemorySourceRecord":
        return cls(
            source_id=str(record["source_id"]),
            source_type=str(record["source_type"]),
            content=str(record.get("content", "")),
            path=record.get("path"),
            version=record.get("version"),
            loaded_at=str(record.get("loaded_at") or _utc_now_iso()),
        )


@dataclass
class MemoryLoadState:
    """Track memory sources loaded for one model turn."""

    loaded_project_memory_paths: dict[str, str] = field(default_factory=dict)
    loaded_nested_memory_paths: dict[str, str] = field(default_factory=dict)
    loaded_skill_reference_paths: dict[str, str] = field(default_factory=dict)
    injected_semantic_memory_ids: set[str] = field(default_factory=set)
    injected_episodic_memory_ids: set[str] = field(default_factory=set)
    memory_file_versions: dict[str, str] = field(default_factory=dict)
    sources: list[MemorySourceRecord] = field(default_factory=list)
    duplicate_sources: list[str] = field(default_factory=list)

    def record_file_source(
        self,
        *,
        path: str,
        source_type: str,
        version: str,
        content: str = "",
    ) -> bool:
        target = self._path_bucket(source_type)
        normalized_path = str(path)
        if normalized_path in target and target[normalized_path] == version:
            self.duplicate_sources.append(normalized_path)
            return False
        target[normalized_path] = version
        self.memory_file_versions[normalized_path] = version
        self.sources.append(
            MemorySourceRecord(
                source_id=_stable_id(source_type, {"path": normalized_path, "version": version}),
                source_type=source_type,
                content=content,
                path=normalized_path,
                version=version,
            )
        )
        return True

    def record_semantic_memory(self, entry: dict[str, Any]) -> bool:
        source_id = self.semantic_memory_id(entry)
        if source_id in self.injected_semantic_memory_ids:
            self.duplicate_sources.append(source_id)
            return False
        self.injected_semantic_memory_ids.add(source_id)
        self.sources.append(
            MemorySourceRecord(
                source_id=source_id,
                source_type="semantic",
                content=str(entry.get("fact", "")),
                version=str(entry.get("updated_at", "")),
            )
        )
        return True

    def record_episodic_memory(self, entry: dict[str, Any]) -> bool:
        source_id = self.episodic_memory_id(entry)
        if source_id in self.injected_episodic_memory_ids:
            self.duplicate_sources.append(source_id)
            return False
        self.injected_episodic_memory_ids.add(source_id)
        self.sources.append(
            MemorySourceRecord(
                source_id=source_id,
                source_type="episodic",
                content=str(entry.get("summary", "")),
                path=entry.get("session_id"),
                version=str(entry.get("updated_at", "")),
            )
        )
        return True

    def semantic_memory_id(self, entry: dict[str, Any]) -> str:
        return _stable_id(
            "semantic",
            {
                "fact": entry.get("normalized_fact") or entry.get("fact", ""),
                "updated_at": entry.get("updated_at", ""),
            },
        )

    def episodic_memory_id(self, entry: dict[str, Any]) -> str:
        return _stable_id(
            "episodic",
            {
                "session_id": entry.get("session_id", ""),
                "summary": entry.get("summary", ""),
                "updated_at": entry.get("updated_at", ""),
            },
        )

    def to_record(self) -> dict[str, Any]:
        return {
            "loaded_project_memory_paths": dict(self.loaded_project_memory_paths),
            "loaded_nested_memory_paths": dict(self.loaded_nested_memory_paths),
            "loaded_skill_reference_paths": dict(self.loaded_skill_reference_paths),
            "injected_semantic_memory_ids": sorted(self.injected_semantic_memory_ids),
            "injected_episodic_memory_ids": sorted(self.injected_episodic_memory_ids),
            "memory_file_versions": dict(self.memory_file_versions),
            "sources": [source.to_record() for source in self.sources],
            "duplicate_sources": list(self.duplicate_sources),
        }

    @classmethod
    def from_record(cls, record: dict[str, Any] | None) -> "MemoryLoadState":
        if not record:
            return cls()
        return cls(
            loaded_project_memory_paths=dict(record.get("loaded_project_memory_paths", {})),
            loaded_nested_memory_paths=dict(record.get("loaded_nested_memory_paths", {})),
            loaded_skill_reference_paths=dict(record.get("loaded_skill_reference_paths", {})),
            injected_semantic_memory_ids=set(record.get("injected_semantic_memory_ids", [])),
            injected_episodic_memory_ids=set(record.get("injected_episodic_memory_ids", [])),
            memory_file_versions=dict(record.get("memory_file_versions", {})),
            sources=[
                MemorySourceRecord.from_record(source)
                for source in record.get("sources", [])
                if isinstance(source, dict)
            ],
            duplicate_sources=list(record.get("duplicate_sources", [])),
        )

    def _path_bucket(self, source_type: str) -> dict[str, str]:
        if source_type == "nested_memory":
            return self.loaded_nested_memory_paths
        if source_type == "skill_reference":
            return self.loaded_skill_reference_paths
        return self.loaded_project_memory_paths


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
        LoopPhase.RUNNING_STOP_HOOKS,
        LoopPhase.COMPLETED,
        LoopPhase.BLOCKED,
        LoopPhase.CANCELLED,
        LoopPhase.FAILED,
    },
    LoopPhase.STREAMING_RESPONSE: {
        LoopPhase.VALIDATING_TOOL_CALLS,
        LoopPhase.RUNNING_STOP_HOOKS,
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


@dataclass
class TurnState:
    """Runtime state for one submitted user message."""

    user_input: str
    turn_id: str = field(default_factory=lambda: f"turn-{uuid4().hex[:8]}")
    status: TurnStatus | str = TurnStatus.PENDING
    usage: TokenUsage = field(default_factory=TokenUsage)
    usage_state: UsageState = field(default_factory=UsageState)
    discovered_skills: set[str] = field(default_factory=set)
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

    def complete(self, final_response: str, usage: TokenUsage | None = None) -> None:
        if usage is not None:
            self.usage = usage.copy()
            self.usage_state.turn = usage.copy()
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

    def to_record(self) -> dict[str, Any]:
        return {
            "turn_id": self.turn_id,
            "user_input": self.user_input,
            "status": _enum_value(self.status),
            "usage": self.usage.to_record(),
            "usage_state": self.usage_state.to_record(),
            "discovered_skills": sorted(self.discovered_skills),
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
        }

    @classmethod
    def from_record(cls, record: dict[str, Any] | None) -> "TurnState":
        if not record:
            return cls(user_input="")
        state = cls(
            turn_id=str(record.get("turn_id") or f"turn-{uuid4().hex[:8]}"),
            user_input=str(record.get("user_input", "")),
            status=TurnStatus(record.get("status", TurnStatus.PENDING.value)),
            usage=TokenUsage.from_record(record.get("usage")),
            usage_state=UsageState.from_record(record.get("usage_state")),
            discovered_skills=set(record.get("discovered_skills", [])),
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
        )
        return state


@dataclass
class ConversationState:
    """Cross-turn state for one conversation/session."""

    session_id: str
    messages: list[dict[str, Any]] = field(default_factory=list)
    usage: TokenUsage = field(default_factory=TokenUsage)
    usage_state: UsageState = field(default_factory=UsageState)
    permissions: PermissionState = field(default_factory=PermissionState)
    file_reads: FileStateCache = field(default_factory=FileStateCache)
    skills: set[str] = field(default_factory=set)
    memories: MemoryLoadState = field(default_factory=MemoryLoadState)
    events: EventBus = field(default_factory=EventBus)
    artifacts: ArtifactStore = field(default_factory=ArtifactStore)
    checkpoints: CheckpointStore = field(default_factory=CheckpointStore)
    cancellation: CancellationController | None = None
    active_turn: TurnState | None = None
    active_task: Any | None = None

    scope: ClassVar[StateScope] = StateScope.CONVERSATION

    def __post_init__(self) -> None:
        if self.cancellation is None:
            self.cancellation = CancellationController(session_id=self.session_id)

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
        if self.cancellation is not None:
            self.active_turn.cancellation_token = self.cancellation.start_turn(
                self.active_turn.turn_id
            )
        self.active_turn.usage_state.set_conversation(self.session_id)
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
            self.usage_state.conversation.add(usage)
        if messages:
            self.append_messages(messages)
        finished = self.active_turn
        self.active_turn = None
        if self.cancellation is not None:
            self.cancellation.finish_turn()
        return finished

    def fail_turn(self, error: str) -> TurnState:
        if self.active_turn is None:
            raise RuntimeError("No active turn to fail.")
        self.active_turn.fail(error)
        failed = self.active_turn
        self.active_turn = None
        if self.cancellation is not None:
            self.cancellation.finish_turn()
        return failed

    def to_record(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "messages": list(self.messages),
            "usage": self.usage.to_record(),
            "usage_state": self.usage_state.to_record(),
            "permissions": self.permissions.to_record(),
            "file_reads": self.file_reads.to_record(),
            "skills": sorted(self.skills),
            "memories": self.memories.to_record(),
            "events": self.events.to_records(),
            "artifacts": self.artifacts.to_records(),
            "checkpoints": self.checkpoints.to_records(),
            "cancellation": (
                self.cancellation.to_record()
                if self.cancellation is not None
                else None
            ),
            "active_turn": (
                self.active_turn.to_record()
                if self.active_turn is not None
                else None
            ),
            "active_task": self.active_task if isinstance(self.active_task, dict) else None,
        }

    @classmethod
    def from_record(cls, record: dict[str, Any] | None) -> "ConversationState":
        if not record:
            return cls(session_id="")
        state = cls(
            session_id=str(record.get("session_id", "")),
            messages=[
                dict(item)
                for item in record.get("messages", [])
                if isinstance(item, dict)
            ],
            usage=TokenUsage.from_record(record.get("usage")),
            usage_state=UsageState.from_record(record.get("usage_state")),
            permissions=PermissionState.from_record(record.get("permissions")),
            file_reads=FileStateCache.from_record(record.get("file_reads")),
            skills=set(record.get("skills", [])),
            memories=MemoryLoadState.from_record(record.get("memories")),
            events=EventBus.from_records(record.get("events")),
            artifacts=ArtifactStore.from_records(record.get("artifacts")),
            checkpoints=CheckpointStore.from_records(record.get("checkpoints")),
            cancellation=CancellationController.from_record(record.get("cancellation")),
            active_turn=(
                TurnState.from_record(record["active_turn"])
                if isinstance(record.get("active_turn"), dict)
                else None
            ),
            active_task=(
                dict(record["active_task"])
                if isinstance(record.get("active_task"), dict)
                else None
            ),
        )
        return state


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
    usage_state: UsageState = field(default_factory=UsageState)
    events: EventBus = field(default_factory=EventBus)
    artifacts: ArtifactStore = field(default_factory=ArtifactStore)
    checkpoints: CheckpointStore = field(default_factory=CheckpointStore)

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

    def to_record(self) -> dict[str, Any]:
        return {
            "configuration": dict(self.configuration),
            "model_registry": dict(self.model_registry),
            "skill_registry": dict(self.skill_registry),
            "mcp_connections": dict(self.mcp_connections),
            "active_session_id": self.active_session_id,
            "conversations": {
                session_id: conversation.to_record()
                for session_id, conversation in self.conversations.items()
            },
            "usage_state": self.usage_state.to_record(),
            "events": self.events.to_records(),
            "artifacts": self.artifacts.to_records(),
            "checkpoints": self.checkpoints.to_records(),
        }

    @classmethod
    def from_record(cls, record: dict[str, Any] | None) -> "ApplicationState":
        if not record:
            return cls()
        return cls(
            configuration=dict(record.get("configuration", {})),
            model_registry=dict(record.get("model_registry", {})),
            skill_registry=dict(record.get("skill_registry", {})),
            mcp_connections=dict(record.get("mcp_connections", {})),
            active_session_id=record.get("active_session_id"),
            conversations={
                str(session_id): ConversationState.from_record(conversation)
                for session_id, conversation in record.get("conversations", {}).items()
                if isinstance(conversation, dict)
            },
            usage_state=UsageState.from_record(record.get("usage_state")),
            events=EventBus.from_records(record.get("events")),
            artifacts=ArtifactStore.from_records(record.get("artifacts")),
            checkpoints=CheckpointStore.from_records(record.get("checkpoints")),
        )
