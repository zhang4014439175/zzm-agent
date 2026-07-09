from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from zzm_agent.core.agent_loop import AgentLoop
from zzm_agent.core.language_policy import (
    ResponseLanguageDecision,
    resolve_response_language,
)
from zzm_agent.core.model_stream import ModelStreamEvent, ModelStreamEventKind
from zzm_agent.core.runtime_state import ApplicationState, ConversationState, TurnState
from zzm_agent.core.state_serialization import StateSnapshotStore


StreamEventCallback = Callable[[ModelStreamEvent], None]


@dataclass(frozen=True)
class QueryResult:
    """Result returned by QueryEngine after one submitted user message."""

    reply: str
    events: list[ModelStreamEvent] = field(default_factory=list)
    turn: TurnState | None = None
    response_language: ResponseLanguageDecision | None = None


class QueryEngine:
    """Cross-turn orchestration boundary shared by CLI and future clients."""

    def __init__(
        self,
        *,
        agent_loop: AgentLoop,
        config: dict[str, Any] | None = None,
        application_state: ApplicationState | None = None,
        conversation_state: ConversationState | None = None,
        snapshot_store: StateSnapshotStore[ConversationState] | None = None,
    ) -> None:
        self.agent_loop = agent_loop
        self.config = config or {}
        session_id = str(getattr(agent_loop.store, "session_id", "default"))
        self.application_state = application_state or ApplicationState(
            active_session_id=session_id
        )
        self.conversation_state = conversation_state or ConversationState(
            session_id=session_id
        )
        self.snapshot_store = snapshot_store
        self.application_state.active_session_id = self.conversation_state.session_id
        self.application_state.conversations[self.conversation_state.session_id] = (
            self.conversation_state
        )

    @classmethod
    def with_snapshot_path(
        cls,
        *,
        agent_loop: AgentLoop,
        snapshot_path: str | Path,
        config: dict[str, Any] | None = None,
        application_state: ApplicationState | None = None,
        conversation_state: ConversationState | None = None,
    ) -> "QueryEngine":
        return cls(
            agent_loop=agent_loop,
            config=config,
            application_state=application_state,
            conversation_state=conversation_state,
            snapshot_store=StateSnapshotStore(snapshot_path),
        )

    def submit_message(
        self,
        user_input: str,
        *,
        stream: bool = True,
        on_stream_event: StreamEventCallback | None = None,
        on_text_chunk: Callable[[str], None] | None = None,
        language_input: str | None = None,
    ) -> QueryResult:
        events: list[ModelStreamEvent] = []

        def emit(event: ModelStreamEvent) -> None:
            events.append(event)
            if on_stream_event is not None:
                on_stream_event(event)

        language_decision = resolve_response_language(
            language_input if language_input is not None else user_input,
            previous_language=self.conversation_state.response_language,
            config=self.config,
        )
        self.conversation_state.response_language = language_decision.language
        self.conversation_state.response_language_source = language_decision.source

        self.conversation_state.active_turn = TurnState(user_input=user_input)
        self.conversation_state.active_turn.start()
        emit(
            ModelStreamEvent.status(
                "turn.started",
                response_language=language_decision.language,
                language_source=language_decision.source,
            )
        )
        self._save_snapshot("turn.started")

        try:
            reply = self.agent_loop.run(
                user_input,
                stream=stream,
                on_text_chunk=on_text_chunk,
                on_stream_event=emit,
                runtime_instructions=[language_decision.instruction],
            )
        except Exception as exc:
            if self.conversation_state.active_turn is not None:
                self.conversation_state.active_turn.fail(str(exc))
            emit(ModelStreamEvent.error(str(exc)))
            self._save_snapshot("turn.failed")
            raise

        last_turn = self.agent_loop.last_turn_state
        if last_turn is not None:
            self.conversation_state.active_turn = last_turn
        elif self.conversation_state.active_turn is not None:
            self.conversation_state.active_turn.complete(reply)
        self.conversation_state.usage = self.agent_loop.cumulative_usage
        self.conversation_state.usage_state = self.agent_loop.usage_state
        self.conversation_state.permissions = self.agent_loop.permission_state
        if not events or events[-1].kind is not ModelStreamEventKind.FINAL_MESSAGE:
            emit(ModelStreamEvent.final_message(reply))
        self._save_snapshot("turn.completed")
        return QueryResult(
            reply=reply,
            events=events,
            turn=self.conversation_state.active_turn,
            response_language=language_decision,
        )

    def _save_snapshot(self, reason: str) -> None:
        if self.snapshot_store is None:
            return
        self.snapshot_store.save(
            self.conversation_state,
            state_type="conversation",
            metadata={
                "reason": reason,
                "session_id": self.conversation_state.session_id,
            },
        )
