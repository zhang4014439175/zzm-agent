"""工作区授权、执行、Effect 与撤销边界。"""

from zzm_agent.workspace.effects import EffectRecord, EffectUndoResult
from zzm_agent.workspace.filesystem import WorkspaceFilesystem
from zzm_agent.workspace.git import WorkspaceGit
from zzm_agent.workspace.process import WorkspaceProcess
from zzm_agent.workspace.runtime import WorkspaceRuntime

__all__ = [
    "EffectRecord",
    "EffectUndoResult",
    "WorkspaceFilesystem",
    "WorkspaceGit",
    "WorkspaceProcess",
    "WorkspaceRuntime",
]
