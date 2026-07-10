from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Sequence


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

    def __init__(self, workspace: Path | str) -> None:
        self.workspace = Path(workspace).resolve()
        self._undo: tuple[str, tuple[str, ...]] | None = None

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
        self._run("add", "--", *clean)
        self._undo = ("restore", ("--staged", "--", *clean))

    def unstage(self, paths: Sequence[str], *, confirm: Callable[[str], bool]) -> None:
        clean = self._validate_paths(paths)
        if not confirm(f"Unstage with git restore --staged -- {self._display(clean)}?"):
            raise GitWorkflowError("Git unstage was not approved.")
        self._run("restore", "--staged", "--", *clean)
        self._undo = ("add", ("--", *clean))

    def undo_last_index_change(self, *, confirm: Callable[[str], bool]) -> str:
        if self._undo is None:
            raise GitWorkflowError("No stage/unstage operation is available to undo.")
        command, args = self._undo
        rendered = "git " + " ".join((command, *args))
        if not confirm(f"Undo the last index change with {rendered}?"):
            raise GitWorkflowError("Git undo was not approved.")
        self._run(command, *args)
        self._undo = None
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
