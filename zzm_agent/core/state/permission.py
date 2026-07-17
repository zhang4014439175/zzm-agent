from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any
from uuid import uuid4

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

