import json
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from zzm_agent.core.agent_loop import AgentLoop
from zzm_agent.core.observability import TokenUsage
from zzm_agent.core.progress_monitor import ProgressSignal, ToolObservation
from zzm_agent.core.runtime_state import (
    ApplicationState,
    ConversationState,
    LoopPhase,
    LoopState,
    TurnState,
    TurnStatus,
)
from zzm_agent.core.state_lifecycle import (
    PersistenceBoundary,
    RecoveryStrategy,
    StateScope,
)
from zzm_agent.core.tool_registry import ToolRegistry


class FakeStore:
    def __init__(self):
        self.history: list[dict] = []
        self.latest_context: dict | None = None

    def load_history(self) -> list[dict]:
        return list(self.history)

    def build_turn_messages(
        self,
        *,
        system_prompt: str,
        user_input: str,
        memory_limit: int,
    ) -> tuple[list[dict], dict]:
        return [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_input},
        ], {"total_tokens": 10, "max_context_tokens": 100}

    def append(self, messages: list[dict]) -> None:
        self.history.extend(messages)

    def save_latest_context(self, payload: dict) -> None:
        self.latest_context = payload


def make_response(content=None, tool_calls=None):
    msg = MagicMock()
    msg.content = content
    msg.tool_calls = tool_calls
    msg.role = "assistant"
    return SimpleNamespace(choices=[SimpleNamespace(message=msg)], usage=None, error=None)


def make_tool_call(name: str, arguments: dict):
    tool_call = MagicMock()
    tool_call.id = "call_1"
    tool_call.function.name = name
    tool_call.function.arguments = json.dumps(arguments)
    return tool_call


def test_application_state_owns_conversations_and_active_session():
    app = ApplicationState(configuration={"model": "demo"})

    conversation = app.get_or_create_conversation("session-a")

    assert ApplicationState.scope is StateScope.APPLICATION
    assert ApplicationState.policy().persistence is PersistenceBoundary.MEMORY_ONLY
    assert app.active_session_id == "session-a"
    assert app.active_conversation() is conversation
    assert app.get_or_create_conversation("session-a") is conversation


def test_conversation_state_starts_and_finishes_turn():
    conversation = ConversationState(session_id="session-a")
    usage = TokenUsage(prompt_tokens=10, completion_tokens=5, total_tokens=15, source="api")

    turn = conversation.start_turn("hello", turn_id="turn-1")
    loop = turn.start_loop()
    finished = conversation.finish_turn(
        "hi",
        messages=[
            {"role": "user", "content": "hello"},
            {"role": "assistant", "content": "hi"},
        ],
        usage=usage,
    )

    assert finished is turn
    assert turn.status is TurnStatus.COMPLETED
    assert turn.final_response == "hi"
    assert loop.phase is LoopPhase.COMPLETED
    assert conversation.active_turn is None
    assert conversation.usage.total_tokens == 15
    assert len(conversation.messages) == 2


def test_conversation_state_rejects_overlapping_active_turns():
    conversation = ConversationState(session_id="session-a")
    conversation.start_turn("first")

    with pytest.raises(RuntimeError, match="another turn is active"):
        conversation.start_turn("second")


def test_turn_state_tracks_skills_memory_permissions_artifacts_and_failure():
    turn = TurnState(user_input="inspect")
    turn.start()
    turn.discovered_skills.add("python")
    turn.loaded_memory_paths.add("memory/project.md")
    turn.permission_requests.append({"tool": "shell"})
    turn.permission_denials.append({"tool": "shell", "reason": "user"})
    turn.artifacts.append({"path": "report.md"})

    turn.fail("boom")

    assert TurnState.scope is StateScope.TURN
    assert TurnState.policy().recovery is RecoveryStrategy.ROLLBACK_PENDING
    assert turn.status is TurnStatus.FAILED
    assert turn.error == "boom"
    assert turn.discovered_skills == {"python"}
    assert turn.loaded_memory_paths == {"memory/project.md"}
    assert turn.permission_requests[0]["tool"] == "shell"
    assert turn.permission_denials[0]["reason"] == "user"
    assert turn.artifacts[0]["path"] == "report.md"


def test_loop_state_tracks_iterations_observations_and_reflection():
    loop = LoopState()
    signal = ProgressSignal(
        reason="repeated_observation",
        round_count=2,
        detail="same result",
    )
    observation = ToolObservation(
        tool_name="search",
        arguments='{"query":"x"}',
        content="no matches",
        success=True,
        retryable=False,
    )

    loop.record_model_call()
    loop.record_tool_round(
        [{"id": "call_1", "function": {"name": "search"}}],
        [observation],
    )
    loop.record_reflection(signal)

    assert LoopState.scope is StateScope.LOOP
    assert LoopState.policy().owner == "AgentLoop"
    assert loop.model_iterations == 1
    assert loop.tool_iterations == 1
    assert loop.reflection_count == 1
    assert loop.phase is LoopPhase.REFLECTING
    assert loop.needs_follow_up is True
    assert loop.progress_signal is signal
    assert loop.observations == [observation]


def test_loop_state_stop_hook_flags_are_separate_from_reflection():
    loop = LoopState()

    loop.activate_stop_hook()
    loop.activate_stop_hook()
    loop.clear_stop_hook()

    assert loop.stop_hook_active is False
    assert loop.stop_hook_attempts == 2
    assert loop.reflection_count == 0


def test_agent_loop_populates_turn_and_loop_state_for_simple_reply():
    registry = ToolRegistry()
    loop = AgentLoop(
        client=MagicMock(),
        model="test-model",
        system_prompt="sys",
        registry=registry,
        store=FakeStore(),
    )
    loop.client.chat.completions.create.return_value = make_response(content="hello")

    result = loop.run("hi", stream=False)

    assert result == "hello"
    assert loop.last_turn_state is not None
    assert loop.last_turn_state.status is TurnStatus.COMPLETED
    assert loop.last_turn_state.final_response == "hello"
    assert loop.last_loop_state is not None
    assert loop.last_loop_state.model_iterations == 1
    assert loop.last_loop_state.tool_iterations == 0
    assert loop.last_loop_state.phase is LoopPhase.COMPLETED


def test_agent_loop_populates_loop_state_for_tool_round():
    registry = ToolRegistry()

    @registry.tool(description="echo")
    def echo(text: str) -> str:
        return f"ECHO:{text}"

    loop = AgentLoop(
        client=MagicMock(),
        model="test-model",
        system_prompt="sys",
        registry=registry,
        store=FakeStore(),
    )
    loop.client.chat.completions.create.side_effect = [
        make_response(tool_calls=[make_tool_call("echo", {"text": "world"})]),
        make_response(content="done"),
    ]

    result = loop.run("call echo", stream=False)

    assert result == "done"
    assert loop.last_turn_state is not None
    assert loop.last_turn_state.status is TurnStatus.COMPLETED
    assert loop.last_loop_state is not None
    assert loop.last_loop_state.model_iterations == 2
    assert loop.last_loop_state.tool_iterations == 1
    assert loop.last_loop_state.phase is LoopPhase.COMPLETED
    assert loop.last_loop_state.needs_follow_up is False
    assert len(loop.last_loop_state.observations) == 1
    assert loop.last_loop_state.observations[0].content == "ECHO:world"
