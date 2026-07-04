from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from typing import Any, Callable


Message = dict[str, Any]


def copy_message(message: Message) -> Message:
    """Return an isolated copy of one chat message."""
    return deepcopy(message)


def copy_messages(messages: list[Message]) -> list[Message]:
    """Return isolated copies of chat messages."""
    return [copy_message(message) for message in messages]


@dataclass
class ConversationMessageStore:
    """Runtime message ledger for one conversation turn.

    persisted_messages are already committed history.
    runtime_messages are the mutable in-flight view used by the loop.
    pending_messages are current-turn messages that should be persisted on commit.
    model_context_messages are the exact message view prepared for the next model call.
    """

    persisted_messages: list[Message] = field(default_factory=list)
    runtime_messages: list[Message] = field(default_factory=list)
    pending_messages: list[Message] = field(default_factory=list)
    model_context_messages: list[Message] = field(default_factory=list)
    committed_messages: list[Message] = field(default_factory=list)

    @classmethod
    def begin_turn(
        cls,
        *,
        persisted_messages: list[Message] | None,
        model_context_messages: list[Message],
        user_message: Message,
    ) -> "ConversationMessageStore":
        """Create a ledger for a new turn from the compressed model context."""
        ledger = cls(
            persisted_messages=copy_messages(persisted_messages or []),
            runtime_messages=copy_messages(model_context_messages),
            model_context_messages=copy_messages(model_context_messages),
        )
        ledger.pending_messages.append(copy_message(user_message))
        return ledger

    def append_pending(self, message: Message) -> None:
        """Append a message that belongs to the turn and should be committed."""
        copied = copy_message(message)
        self.runtime_messages.append(copied)
        self.pending_messages.append(copy_message(message))

    def append_runtime_only(self, message: Message) -> None:
        """Append a message visible to the model but never persisted as history."""
        self.runtime_messages.append(copy_message(message))

    def prepare_model_context(self) -> list[Message]:
        """Freeze and return the current message view for the next model call."""
        self.model_context_messages = copy_messages(self.runtime_messages)
        return copy_messages(self.model_context_messages)

    def commit(self, append: Callable[[list[Message]], None]) -> list[Message]:
        """Atomically persist pending messages and clear the pending buffer."""
        committed = copy_messages(self.pending_messages)
        append(committed)
        self.persisted_messages.extend(copy_messages(committed))
        self.committed_messages.extend(copy_messages(committed))
        self.pending_messages.clear()
        return committed

    def rollback_pending(self) -> list[Message]:
        """Drop current-turn pending messages without touching committed history."""
        rolled_back = copy_messages(self.pending_messages)
        self.pending_messages.clear()
        return rolled_back

    def pending_count(self) -> int:
        return len(self.pending_messages)

    def runtime_count(self) -> int:
        return len(self.runtime_messages)
