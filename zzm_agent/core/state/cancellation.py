from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


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

