"""8.8 状态模块拆分的兼容性特征测试。"""

from zzm_agent.core import runtime_state
from zzm_agent.core.state.application import ApplicationState
from zzm_agent.core.state.cancellation import CancellationController, CancellationToken
from zzm_agent.core.state.conversation import ConversationState
from zzm_agent.core.state.loop import LoopState
from zzm_agent.core.state.permission import PermissionState
from zzm_agent.core.state.turn import TurnState


def test_runtime_state_facade_exports_moved_state_definitions():
    """旧导入门面应返回新模块中的同一类，而不是复制兼容子类。"""
    assert runtime_state.ApplicationState is ApplicationState
    assert runtime_state.ConversationState is ConversationState
    assert runtime_state.TurnState is TurnState
    assert runtime_state.LoopState is LoopState
    assert runtime_state.PermissionState is PermissionState
    assert runtime_state.CancellationController is CancellationController
    assert runtime_state.CancellationToken is CancellationToken


def test_state_classes_report_their_new_ownership_modules():
    """六类核心状态定义应真正归属拆分后的模块。"""
    assert ApplicationState.__module__ == "zzm_agent.core.state.application"
    assert ConversationState.__module__ == "zzm_agent.core.state.conversation"
    assert TurnState.__module__ == "zzm_agent.core.state.turn"
    assert LoopState.__module__ == "zzm_agent.core.state.loop"
    assert PermissionState.__module__ == "zzm_agent.core.state.permission"
    assert CancellationController.__module__ == "zzm_agent.core.state.cancellation"


def test_facade_round_trip_keeps_nested_state_types_and_schema():
    """旧入口序列化再恢复后，应保留新模块嵌套类型与原字段。"""
    application = runtime_state.ApplicationState()
    conversation = application.get_or_create_conversation("session-1")
    turn = conversation.start_turn("hello")
    turn.start_loop()

    restored = runtime_state.ApplicationState.from_record(application.to_record())

    restored_conversation = restored.conversations["session-1"]
    assert isinstance(restored_conversation, ConversationState)
    assert isinstance(restored_conversation.active_turn, TurnState)
    assert isinstance(restored_conversation.active_turn.loop, LoopState)
    assert restored.to_record() == application.to_record()
