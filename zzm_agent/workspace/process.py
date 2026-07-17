from __future__ import annotations

from typing import Callable, TypeVar

from zzm_agent.workspace.runtime import WorkspaceRuntime


T = TypeVar("T")


class WorkspaceProcess:
    """通过 WorkspaceRuntime 执行不可逆进程副作用的薄适配器。"""

    def __init__(self, runtime: WorkspaceRuntime) -> None:
        self.runtime = runtime

    def execute(self, command: str, action: Callable[[], T], *, cwd: str) -> T:
        """授权、执行并记录一个 Shell/进程 Effect。"""
        return self.runtime.execute(
            kind="process",
            operation="execute",
            target=cwd,
            action=action,
            metadata={"command": command},
        )
