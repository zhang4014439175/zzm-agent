"""Persistent, conflict-aware records for agent-managed file changes."""

from __future__ import annotations

import difflib
import hashlib
import json
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from zzm_agent.constants import ZZM_AGENT_DIR
from zzm_agent.core.observability import ToolEvent


_FILE_MUTATION_TOOLS = {"file_edit", "write_file", "file_append"}


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _hash_text(content: str | None) -> str | None:
    if content is None:
        return None
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


@dataclass
class ChangeSet:
    """One successful, reversible agent file mutation."""

    change_set_id: str
    path: str
    tool_name: str
    tool_call_id: str
    session_id: str
    turn_id: str | None
    created_at: str
    before_content: str | None
    after_content: str | None
    before_hash: str | None
    after_hash: str | None
    patch: str
    status: str = "applied"
    reverted_at: str | None = None
    conflict_message: str | None = None

    @classmethod
    def from_record(cls, record: dict[str, Any]) -> "ChangeSet":
        return cls(**{key: record.get(key) for key in cls.__dataclass_fields__})

    def to_record(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class UndoResult:
    """The observable outcome of attempting to reverse a change set."""

    change_set: ChangeSet | None
    undone: bool
    message: str


@dataclass
class _PendingChange:
    path: Path
    before_content: str | None


class ChangeSetStore:
    """Capture managed writes and persist their reversible text snapshots.

    The store deliberately observes the same tool lifecycle as the CLI rather
    than trusting a tool's success text.  A change is recorded only after a
    successful tool event and only when the file bytes actually changed.
    """

    def __init__(self, workspace_root: str | Path, *, session_id: str = "default") -> None:
        self.workspace_root = Path(workspace_root).expanduser().resolve()
        self.session_id = session_id
        self.path = self.workspace_root / ZZM_AGENT_DIR / "changesets.json"
        self._pending: dict[str, _PendingChange] = {}
        self._changesets = self._load()

    def capture_start(self, event: ToolEvent) -> None:
        path = self._event_path(event)
        if path is None:
            return
        self._pending[event.tool_call_id] = _PendingChange(
            path=path,
            before_content=self._read_text_or_none(path),
        )

    def capture_end(self, event: ToolEvent, *, turn_id: str | None = None) -> ChangeSet | None:
        pending = self._pending.pop(event.tool_call_id, None)
        if pending is None or event.status != "success":
            return None
        after_content = self._read_text_or_none(pending.path)
        if after_content == pending.before_content:
            return None

        relative_path = str(pending.path.relative_to(self.workspace_root))
        change = ChangeSet(
            change_set_id=f"changeset-{uuid4().hex[:12]}",
            path=relative_path,
            tool_name=event.tool_name,
            tool_call_id=event.tool_call_id,
            session_id=self.session_id,
            turn_id=turn_id,
            created_at=_utc_now(),
            before_content=pending.before_content,
            after_content=after_content,
            before_hash=_hash_text(pending.before_content),
            after_hash=_hash_text(after_content),
            patch=self._make_patch(relative_path, pending.before_content, after_content),
        )
        self._changesets.append(change)
        self._save()
        return change

    def list_changesets(self, *, session_id: str | None = None) -> list[ChangeSet]:
        selected = [
            change for change in self._changesets
            if session_id is None or change.session_id == session_id
        ]
        return list(reversed(selected))

    def undo(self, change_set_id: str | None = None) -> UndoResult:
        change = self._select_undo_target(change_set_id)
        if change is None:
            return UndoResult(None, False, "No managed file change is available to undo.")

        target = (self.workspace_root / change.path).resolve(strict=False)
        if not target.is_relative_to(self.workspace_root):
            return UndoResult(None, False, "Stored change path is outside the workspace and was rejected.")
        current = self._read_text_or_none(target)
        if _hash_text(current) != change.after_hash:
            change.status = "conflicted"
            change.conflict_message = (
                "The file no longer matches the version written by this change set; "
                "it may have been edited by you or another tool."
            )
            self._save()
            return UndoResult(change, False, f"Undo conflict for {change.path}: {change.conflict_message}")

        if change.before_content is None:
            if target.exists():
                target.unlink()
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(change.before_content, encoding="utf-8")
        change.status = "reverted"
        change.reverted_at = _utc_now()
        change.conflict_message = None
        self._save()
        return UndoResult(change, True, f"Undid {change.change_set_id}: restored {change.path}.")

    def _select_undo_target(self, change_set_id: str | None) -> ChangeSet | None:
        if change_set_id:
            return next(
                (change for change in self._changesets if change.change_set_id == change_set_id and change.status == "applied"),
                None,
            )
        return next(
            (change for change in reversed(self._changesets) if change.session_id == self.session_id and change.status == "applied"),
            None,
        )

    def _event_path(self, event: ToolEvent) -> Path | None:
        if event.tool_name.rsplit(".", 1)[-1] not in _FILE_MUTATION_TOOLS:
            return None
        raw_path = event.arguments_summary.get("path")
        if not isinstance(raw_path, str) or not raw_path or "... (" in raw_path:
            return None
        candidate = Path(raw_path).expanduser()
        resolved = (candidate if candidate.is_absolute() else self.workspace_root / candidate).resolve(strict=False)
        if not resolved.is_relative_to(self.workspace_root):
            return None
        return resolved

    @staticmethod
    def _read_text_or_none(path: Path) -> str | None:
        if not path.exists() or not path.is_file():
            return None
        return path.read_text(encoding="utf-8", errors="replace")

    @staticmethod
    def _make_patch(path: str, before: str | None, after: str | None) -> str:
        return "".join(
            difflib.unified_diff(
                (before or "").splitlines(keepends=True),
                (after or "").splitlines(keepends=True),
                fromfile=f"a/{path}",
                tofile=f"b/{path}",
                n=3,
            )
        )

    def _load(self) -> list[ChangeSet]:
        if not self.path.exists():
            return []
        try:
            records = json.loads(self.path.read_text(encoding="utf-8"))
            return [ChangeSet.from_record(record) for record in records if isinstance(record, dict)]
        except (OSError, json.JSONDecodeError, TypeError):
            return []

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text(
            json.dumps([change.to_record() for change in self._changesets], ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        temporary.replace(self.path)
