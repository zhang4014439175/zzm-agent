from __future__ import annotations

from pathlib import Path
from typing import Callable, TypeVar

from zzm_agent.workspace.runtime import WorkspaceRuntime


T = TypeVar("T")


class WorkspaceFilesystem:
    """通过 WorkspaceRuntime 执行文件系统副作用的薄适配器。"""

    def __init__(self, runtime: WorkspaceRuntime) -> None:
        self.runtime = runtime

    def mutate(self, path: str | Path, operation: str, action: Callable[[], T]) -> T:
        """执行带检查点和撤销记录的文件变更。"""
        return self.runtime.execute_file_mutation(path, operation=operation, action=action)
