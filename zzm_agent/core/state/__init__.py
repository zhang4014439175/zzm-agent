"""按生命周期职责拆分的运行时状态对象。"""

from zzm_agent.core.state.application import ApplicationState
from zzm_agent.core.state.cancellation import (
    CancellationController,
    CancellationError,
    CancellationToken,
)
from zzm_agent.core.state.conversation import ConversationState
from zzm_agent.core.state.loop import (
    LoopPhase,
    LoopState,
    LoopTransition,
    LoopTransitionError,
)
from zzm_agent.core.state.permission import (
    PermissionDecision,
    PermissionRequest,
    PermissionScope,
    PermissionState,
    PermissionStatus,
    summarize_permission_arguments,
)
from zzm_agent.core.state.support import (
    FileReadRange,
    FileState,
    FileStateCache,
    MemoryLoadState,
    MemorySourceRecord,
)
from zzm_agent.core.state.turn import TurnState, TurnStatus, TurnTermination

__all__ = [
    "ApplicationState",
    "CancellationController",
    "CancellationError",
    "CancellationToken",
    "ConversationState",
    "FileReadRange",
    "FileState",
    "FileStateCache",
    "LoopPhase",
    "LoopState",
    "LoopTransition",
    "LoopTransitionError",
    "MemoryLoadState",
    "MemorySourceRecord",
    "PermissionDecision",
    "PermissionRequest",
    "PermissionScope",
    "PermissionState",
    "PermissionStatus",
    "TurnState",
    "TurnStatus",
    "TurnTermination",
    "summarize_permission_arguments",
]
