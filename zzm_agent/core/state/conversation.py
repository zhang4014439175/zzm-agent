from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, ClassVar
from uuid import uuid4

from zzm_agent.core.observability import TokenUsage, UsageState
from zzm_agent.core.runtime_records import ArtifactStore, CheckpointStore, EventBus
from zzm_agent.core.state_lifecycle import StateLifecyclePolicy, StateScope, get_state_policy
from zzm_agent.core.state.cancellation import CancellationController
from zzm_agent.core.state.permission import PermissionState
from zzm_agent.core.state.support import FileStateCache, MemoryLoadState
from zzm_agent.core.state.turn import TurnState, TurnStatus

@dataclass
class ConversationState:
    """Cross-turn state for one conversation/session."""

    session_id: str
    messages: list[dict[str, Any]] = field(default_factory=list)
    usage: TokenUsage = field(default_factory=TokenUsage)
    usage_state: UsageState = field(default_factory=UsageState)
    permissions: PermissionState = field(default_factory=PermissionState)
    file_reads: FileStateCache = field(default_factory=FileStateCache)
    skills: set[str] = field(default_factory=set)
    memories: MemoryLoadState = field(default_factory=MemoryLoadState)
    events: EventBus = field(default_factory=EventBus)
    artifacts: ArtifactStore = field(default_factory=ArtifactStore)
    checkpoints: CheckpointStore = field(default_factory=CheckpointStore)
    response_language: str | None = None
    response_language_source: str | None = None
    cancellation: CancellationController | None = None
    active_turn: TurnState | None = None
    active_task: Any | None = None

    scope: ClassVar[StateScope] = StateScope.CONVERSATION

    def __post_init__(self) -> None:
        if self.cancellation is None:
            self.cancellation = CancellationController(session_id=self.session_id)

    @classmethod
    def policy(cls) -> StateLifecyclePolicy:
        return get_state_policy(cls.scope)

    def append_messages(self, messages: list[dict[str, Any]]) -> None:
        self.messages.extend(messages)

    def start_turn(self, user_input: str, turn_id: str | None = None) -> TurnState:
        if self.active_turn is not None and self.active_turn.status in {
            TurnStatus.PENDING,
            TurnStatus.IN_PROGRESS,
        }:
            raise RuntimeError("Cannot start a new turn while another turn is active.")
        self.active_turn = TurnState(
            turn_id=turn_id or f"turn-{uuid4().hex[:8]}",
            user_input=user_input,
        )
        if self.cancellation is not None:
            self.active_turn.cancellation_token = self.cancellation.start_turn(
                self.active_turn.turn_id
            )
        self.active_turn.usage_state.set_conversation(self.session_id)
        self.active_turn.start()
        return self.active_turn

    def finish_turn(
        self,
        final_response: str,
        *,
        messages: list[dict[str, Any]] | None = None,
        usage: TokenUsage | None = None,
    ) -> TurnState:
        if self.active_turn is None:
            raise RuntimeError("No active turn to finish.")
        self.active_turn.complete(final_response, usage=usage)
        if usage is not None:
            self.usage.add(usage)
            self.usage_state.conversation.add(usage)
        if messages:
            self.append_messages(messages)
        finished = self.active_turn
        self.active_turn = None
        if self.cancellation is not None:
            self.cancellation.finish_turn()
        return finished

    def fail_turn(self, error: str) -> TurnState:
        if self.active_turn is None:
            raise RuntimeError("No active turn to fail.")
        self.active_turn.fail(error)
        failed = self.active_turn
        self.active_turn = None
        if self.cancellation is not None:
            self.cancellation.finish_turn()
        return failed

    def to_record(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "messages": list(self.messages),
            "usage": self.usage.to_record(),
            "usage_state": self.usage_state.to_record(),
            "permissions": self.permissions.to_record(),
            "file_reads": self.file_reads.to_record(),
            "skills": sorted(self.skills),
            "memories": self.memories.to_record(),
            "events": self.events.to_records(),
            "artifacts": self.artifacts.to_records(),
            "checkpoints": self.checkpoints.to_records(),
            "response_language": self.response_language,
            "response_language_source": self.response_language_source,
            "cancellation": (
                self.cancellation.to_record()
                if self.cancellation is not None
                else None
            ),
            "active_turn": (
                self.active_turn.to_record()
                if self.active_turn is not None
                else None
            ),
            "active_task": self.active_task if isinstance(self.active_task, dict) else None,
        }

    @classmethod
    def from_record(cls, record: dict[str, Any] | None) -> "ConversationState":
        if not record:
            return cls(session_id="")
        state = cls(
            session_id=str(record.get("session_id", "")),
            messages=[
                dict(item)
                for item in record.get("messages", [])
                if isinstance(item, dict)
            ],
            usage=TokenUsage.from_record(record.get("usage")),
            usage_state=UsageState.from_record(record.get("usage_state")),
            permissions=PermissionState.from_record(record.get("permissions")),
            file_reads=FileStateCache.from_record(record.get("file_reads")),
            skills=set(record.get("skills", [])),
            memories=MemoryLoadState.from_record(record.get("memories")),
            events=EventBus.from_records(record.get("events")),
            artifacts=ArtifactStore.from_records(record.get("artifacts")),
            checkpoints=CheckpointStore.from_records(record.get("checkpoints")),
            response_language=record.get("response_language"),
            response_language_source=record.get("response_language_source"),
            cancellation=CancellationController.from_record(record.get("cancellation")),
            active_turn=(
                TurnState.from_record(record["active_turn"])
                if isinstance(record.get("active_turn"), dict)
                else None
            ),
            active_task=(
                dict(record["active_task"])
                if isinstance(record.get("active_task"), dict)
                else None
            ),
        )
        return state

