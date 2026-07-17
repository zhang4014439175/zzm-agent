from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Sequence

from zzm_agent.workspace.git import WorkspaceGit
from zzm_agent.workspace.runtime import WorkspaceRuntime


class GitWorkflowError(RuntimeError):
    """Raised when a Git workflow command cannot be completed safely."""


@dataclass(frozen=True)
class GitSnapshot:
    branch: str
    status: str
    staged_diff: str
    unstaged_diff: str


class GitWorkflow:
    """Small, auditable Git service used by slash commands."""

    def __init__(
        self,
        workspace: Path | str,
        *,
        workspace_runtime: WorkspaceRuntime | None = None,
    ) -> None:
        self.workspace = Path(workspace).resolve()
        self.workspace_runtime = workspace_runtime or WorkspaceRuntime(self.workspace)
        self.effects = WorkspaceGit(self.workspace_runtime)
        self._undo: tuple[str, tuple[str, ...]] | None = None
        self._undo_effect_id: str | None = None

    def snapshot(self) -> GitSnapshot:
        return GitSnapshot(
            branch=self._run("branch", "--show-current").strip() or "(detached HEAD)",
            status=self._run("status", "--short"),
            staged_diff=self._run("diff", "--cached"),
            unstaged_diff=self._run("diff"),
        )

    def stage(self, paths: Sequence[str], *, confirm: Callable[[str], bool]) -> None:
        clean = self._validate_paths(paths)
        if not confirm(f"Stage with git add -- {self._display(clean)}?"):
            raise GitWorkflowError("Git stage was not approved.")
        undo_args = ("--staged", "--", *clean)
        self.effects.mutate(
            "stage",
            self._display(clean),
            lambda: self._run("add", "--", *clean),
            undo=lambda: self._run("restore", *undo_args),
        )
        self._undo = ("restore", ("--staged", "--", *clean))
        self._undo_effect_id = self.workspace_runtime.effects[-1].effect_id

    def unstage(self, paths: Sequence[str], *, confirm: Callable[[str], bool]) -> None:
        clean = self._validate_paths(paths)
        if not confirm(f"Unstage with git restore --staged -- {self._display(clean)}?"):
            raise GitWorkflowError("Git unstage was not approved.")
        undo_args = ("--", *clean)
        self.effects.mutate(
            "unstage",
            self._display(clean),
            lambda: self._run("restore", "--staged", "--", *clean),
            undo=lambda: self._run("add", *undo_args),
        )
        self._undo = ("add", ("--", *clean))
        self._undo_effect_id = self.workspace_runtime.effects[-1].effect_id

    def undo_last_index_change(self, *, confirm: Callable[[str], bool]) -> str:
        if self._undo is None:
            raise GitWorkflowError("No stage/unstage operation is available to undo.")
        command, args = self._undo
        rendered = "git " + " ".join((command, *args))
        if not confirm(f"Undo the last index change with {rendered}?"):
            raise GitWorkflowError("Git undo was not approved.")
        result = self.workspace_runtime.undo(self._undo_effect_id)
        if not result.undone:
            raise GitWorkflowError(result.message)
        self._undo = None
        self._undo_effect_id = None
        return rendered

    def _validate_paths(self, paths: Sequence[str]) -> tuple[str, ...]:
        clean = tuple(item.strip() for item in paths if item.strip())
        if not clean:
            raise GitWorkflowError("At least one path is required; use '.' for all changes.")
        if any(item.startswith("-") for item in clean):
            raise GitWorkflowError("Git path arguments cannot start with '-'.")
        return clean

    def _run(self, *args: str) -> str:
        completed = subprocess.run(
            ["git", *args],
            cwd=self.workspace,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
        if completed.returncode != 0:
            detail = completed.stderr.strip() or completed.stdout.strip() or "unknown git error"
            raise GitWorkflowError(detail)
        return completed.stdout

    @staticmethod
    def _display(paths: Sequence[str]) -> str:
        return " ".join(repr(path) for path in paths)
