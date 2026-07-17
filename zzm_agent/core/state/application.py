from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, ClassVar

from zzm_agent.core.observability import UsageState
from zzm_agent.core.runtime_records import ArtifactStore, CheckpointStore, EventBus
from zzm_agent.core.state_lifecycle import StateLifecyclePolicy, StateScope, get_state_policy
from zzm_agent.core.state.conversation import ConversationState

@dataclass
class ApplicationState:
    """Process-level state owned by the application runtime."""

    configuration: dict[str, Any] = field(default_factory=dict)
    model_registry: dict[str, Any] = field(default_factory=dict)
    tool_registry: Any | None = None
    skill_registry: dict[str, Any] = field(default_factory=dict)
    mcp_connections: dict[str, Any] = field(default_factory=dict)
    active_session_id: str | None = None
    conversations: dict[str, ConversationState] = field(default_factory=dict)
    usage_state: UsageState = field(default_factory=UsageState)
    events: EventBus = field(default_factory=EventBus)
    artifacts: ArtifactStore = field(default_factory=ArtifactStore)
    checkpoints: CheckpointStore = field(default_factory=CheckpointStore)

    scope: ClassVar[StateScope] = StateScope.APPLICATION

    @classmethod
    def policy(cls) -> StateLifecyclePolicy:
        return get_state_policy(cls.scope)

    def set_active_session(self, session_id: str) -> None:
        self.active_session_id = session_id

    def get_or_create_conversation(self, session_id: str) -> ConversationState:
        if session_id not in self.conversations:
            self.conversations[session_id] = ConversationState(session_id=session_id)
        self.active_session_id = session_id
        return self.conversations[session_id]

    def active_conversation(self) -> ConversationState | None:
        if self.active_session_id is None:
            return None
        return self.conversations.get(self.active_session_id)

    def to_record(self) -> dict[str, Any]:
        return {
            "configuration": dict(self.configuration),
            "model_registry": dict(self.model_registry),
            "skill_registry": dict(self.skill_registry),
            "mcp_connections": dict(self.mcp_connections),
            "active_session_id": self.active_session_id,
            "conversations": {
                session_id: conversation.to_record()
                for session_id, conversation in self.conversations.items()
            },
            "usage_state": self.usage_state.to_record(),
            "events": self.events.to_records(),
            "artifacts": self.artifacts.to_records(),
            "checkpoints": self.checkpoints.to_records(),
        }

    @classmethod
    def from_record(cls, record: dict[str, Any] | None) -> "ApplicationState":
        if not record:
            return cls()
        return cls(
            configuration=dict(record.get("configuration", {})),
            model_registry=dict(record.get("model_registry", {})),
            skill_registry=dict(record.get("skill_registry", {})),
            mcp_connections=dict(record.get("mcp_connections", {})),
            active_session_id=record.get("active_session_id"),
            conversations={
                str(session_id): ConversationState.from_record(conversation)
                for session_id, conversation in record.get("conversations", {}).items()
                if isinstance(conversation, dict)
            },
            usage_state=UsageState.from_record(record.get("usage_state")),
            events=EventBus.from_records(record.get("events")),
            artifacts=ArtifactStore.from_records(record.get("artifacts")),
            checkpoints=CheckpointStore.from_records(record.get("checkpoints")),
        )

