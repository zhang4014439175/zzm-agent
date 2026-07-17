from __future__ import annotations

from typing import Callable, TypeVar

from zzm_agent.workspace.runtime import WorkspaceRuntime


T = TypeVar("T")


class WorkspaceGit:
    """通过 WorkspaceRuntime 执行 Git 索引副作用的薄适配器。"""

    def __init__(self, runtime: WorkspaceRuntime) -> None:
        self.runtime = runtime

    def mutate(
        self,
        operation: str,
        target: str,
        action: Callable[[], T],
        *,
        undo: Callable[[], None],
    ) -> T:
        """记录可撤销的 Git 索引操作。"""
        return self.runtime.execute(
            kind="git",
            operation=operation,
            target=target,
            action=action,
            reversible=True,
            undo=undo,
        )
