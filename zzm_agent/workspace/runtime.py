from __future__ import annotations

import base64
import json
from pathlib import Path
from typing import Any, Callable, TypeVar

from zzm_agent.workspace.effects import EffectRecord, EffectUndoResult, utc_now


T = TypeVar("T")
Authorization = Callable[[str, str, str, dict[str, Any]], bool]


class WorkspaceRuntime:
    """统一授权、执行、Effect 记录、检查点和撤销的工作区边界。"""

    def __init__(
        self,
        workspace_root: str | Path,
        *,
        authorize: Authorization | None = None,
        journal_path: str | Path | None = None,
    ) -> None:
        self.workspace_root = Path(workspace_root).expanduser().resolve()
        self.authorize = authorize or (lambda _kind, _operation, _target, _meta: True)
        self.journal_path = Path(journal_path) if journal_path else None
        self.effects: list[EffectRecord] = []
        self._undo_actions: dict[str, Callable[[], None]] = {}
        self._load()

    def execute(
        self,
        *,
        kind: str,
        operation: str,
        target: str,
        action: Callable[[], T],
        metadata: dict[str, Any] | None = None,
        reversible: bool = False,
        undo: Callable[[], None] | None = None,
        checkpoint_id: str | None = None,
    ) -> T:
        """在统一边界内授权并执行副作用，同时记录成功或失败事实。"""
        details = dict(metadata or {})
        effect = EffectRecord(
            kind=kind,
            operation=operation,
            target=target,
            reversible=reversible,
            checkpoint_id=checkpoint_id,
            metadata=details,
        )
        effect.authorized = bool(self.authorize(kind, operation, target, details))
        if not effect.authorized:
            effect.status = "denied"
            effect.completed_at = utc_now()
            self._append(effect)
            raise PermissionError(f"Workspace effect was not authorized: {kind}:{operation} {target}")
        effect.status = "running"
        try:
            result = action()
        except Exception as exc:
            effect.status = "failed"
            effect.error = str(exc)
            effect.completed_at = utc_now()
            self._append(effect)
            raise
        effect.status = "applied"
        effect.completed_at = utc_now()
        if reversible and undo is not None:
            self._undo_actions[effect.effect_id] = undo
        self._append(effect)
        return result

    def execute_file_mutation(
        self,
        path: str | Path,
        *,
        operation: str,
        action: Callable[[], T],
        metadata: dict[str, Any] | None = None,
    ) -> T:
        """为文件写操作创建内容检查点并注册冲突感知撤销。"""
        target = Path(path).expanduser().resolve(strict=False)
        if not target.is_relative_to(self.workspace_root):
            raise PermissionError(f"Path is outside workspace: {target}")
        existed = target.exists() and target.is_file()
        before = target.read_bytes() if existed else None
        checkpoint_id = f"file:{target}:{len(self.effects) + 1}"

        result = self.execute(
            kind="file",
            operation=operation,
            target=str(target),
            action=action,
            metadata={
                **dict(metadata or {}),
                "before_exists": existed,
                "before_content_b64": (
                    base64.b64encode(before).decode("ascii") if before is not None else None
                ),
            },
            reversible=True,
            checkpoint_id=checkpoint_id,
        )
        after = target.read_bytes() if target.exists() and target.is_file() else None
        effect = self.effects[-1]
        effect.metadata["after_content_b64"] = (
            base64.b64encode(after).decode("ascii") if after is not None else None
        )

        def undo_file() -> None:
            current = target.read_bytes() if target.exists() and target.is_file() else None
            if current != after:
                raise RuntimeError("File changed after the recorded effect; refusing to overwrite it.")
            if before is None:
                if target.exists():
                    target.unlink()
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(before)

        self._undo_actions[effect.effect_id] = undo_file
        self._save()
        return result

    def undo(self, effect_id: str | None = None) -> EffectUndoResult:
        """撤销指定或最近一个仍处于 applied 状态的可逆 Effect。"""
        effect = next(
            (
                item
                for item in reversed(self.effects)
                if item.status == "applied"
                and item.reversible
                and (effect_id is None or item.effect_id == effect_id)
            ),
            None,
        )
        if effect is None:
            return EffectUndoResult(None, False, "No reversible workspace effect is available.")
        undo = self._undo_actions.get(effect.effect_id)
        if undo is None and effect.kind == "file":
            undo = self._restore_file_action(effect)
        if undo is None:
            return EffectUndoResult(effect, False, "The effect has no available undo action.")
        try:
            undo()
        except Exception as exc:
            effect.status = "conflicted"
            effect.error = str(exc)
            self._save()
            return EffectUndoResult(effect, False, str(exc))
        effect.status = "reverted"
        effect.reverted_at = utc_now()
        effect.error = None
        self._undo_actions.pop(effect.effect_id, None)
        self._save()
        return EffectUndoResult(effect, True, f"Undid {effect.effect_id}.")

    def _restore_file_action(self, effect: EffectRecord) -> Callable[[], None]:
        target = Path(effect.target).resolve(strict=False)
        before_encoded = effect.metadata.get("before_content_b64")
        after_encoded = effect.metadata.get("after_content_b64")
        before = base64.b64decode(before_encoded) if before_encoded is not None else None
        after = base64.b64decode(after_encoded) if after_encoded is not None else None

        def restore() -> None:
            current = target.read_bytes() if target.exists() and target.is_file() else None
            if current != after:
                raise RuntimeError("File changed after the recorded effect; refusing to overwrite it.")
            if before is None:
                if target.exists():
                    target.unlink()
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(before)

        return restore

    def _append(self, effect: EffectRecord) -> None:
        self.effects.append(effect)
        self._save()

    def _load(self) -> None:
        if self.journal_path is None or not self.journal_path.exists():
            return
        try:
            records = json.loads(self.journal_path.read_text(encoding="utf-8"))
            self.effects = [EffectRecord.from_record(item) for item in records if isinstance(item, dict)]
        except (OSError, ValueError, TypeError):
            self.effects = []

    def _save(self) -> None:
        if self.journal_path is None:
            return
        self.journal_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.journal_path.with_suffix(self.journal_path.suffix + ".tmp")
        temporary.write_text(
            json.dumps([item.to_record() for item in self.effects], ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        temporary.replace(self.journal_path)
