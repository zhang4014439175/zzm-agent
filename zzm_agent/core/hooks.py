from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable
from uuid import uuid4


class HookType(str, Enum):
    """Supported runtime hook points."""

    SESSION_START = "session_start"
    SESSION_END = "session_end"
    TURN_START = "turn_start"
    TURN_END = "turn_end"
    BEFORE_MODEL = "before_model"
    AFTER_MODEL = "after_model"
    BEFORE_TOOL = "before_tool"
    AFTER_TOOL = "after_tool"
    TOOL_ERROR = "tool_error"
    STOP = "stop"


class HookDecision(str, Enum):
    """Decision returned by a hook."""

    CONTINUE = "continue"
    BLOCK = "block"
    RETRY = "retry"
    MODIFY = "modify"
    STOP = "stop"


@dataclass
class HookContext:
    """Runtime data passed to one hook invocation."""

    hook_type: HookType
    session_id: str | None = None
    turn_id: str | None = None
    model: str | None = None
    user_input: str | None = None
    messages: list[dict[str, Any]] | None = None
    tools: list[dict[str, Any]] | None = None
    tool_name: str | None = None
    tool_call_id: str | None = None
    tool_arguments: dict[str, Any] | None = None
    tool_result: str | None = None
    risk_level: str | None = None
    final_response: str | None = None
    error: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class HookResult:
    """One hook decision and optional modification payload."""

    decision: HookDecision | str = HookDecision.CONTINUE
    reason: str = ""
    message: str = ""
    modified_messages: list[dict[str, Any]] | None = None
    modified_arguments: dict[str, Any] | None = None
    modified_response: str | None = None
    retry_prompt: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def continue_(cls, reason: str = "") -> "HookResult":
        return cls(decision=HookDecision.CONTINUE, reason=reason)

    @classmethod
    def block(cls, message: str, *, reason: str = "hook_blocked") -> "HookResult":
        return cls(decision=HookDecision.BLOCK, reason=reason, message=message)

    @classmethod
    def retry(
        cls,
        retry_prompt: str,
        *,
        reason: str = "hook_retry",
    ) -> "HookResult":
        return cls(
            decision=HookDecision.RETRY,
            reason=reason,
            retry_prompt=retry_prompt,
        )

    @classmethod
    def modify_response(
        cls,
        response: str,
        *,
        reason: str = "hook_modified",
    ) -> "HookResult":
        return cls(
            decision=HookDecision.MODIFY,
            reason=reason,
            modified_response=response,
        )

    def normalized_decision(self) -> HookDecision:
        if isinstance(self.decision, HookDecision):
            return self.decision
        return HookDecision(str(self.decision))


HookCallback = Callable[[HookContext], HookResult | HookDecision | str | None]


@dataclass
class HookRecord:
    """Registration record for one hook callback."""

    hook_id: str
    hook_type: HookType
    callback: HookCallback
    name: str = ""


class HookRegistry:
    """Synchronous in-process hook registry for AgentLoop lifecycle points."""

    def __init__(self) -> None:
        self._hooks: dict[HookType, list[HookRecord]] = {
            hook_type: [] for hook_type in HookType
        }
        self.invocations: list[dict[str, Any]] = []

    def register(
        self,
        hook_type: HookType | str,
        callback: HookCallback,
        *,
        name: str = "",
    ) -> str:
        normalized = self._coerce_hook_type(hook_type)
        hook_id = f"hook-{uuid4().hex[:12]}"
        self._hooks[normalized].append(
            HookRecord(
                hook_id=hook_id,
                hook_type=normalized,
                callback=callback,
                name=name or getattr(callback, "__name__", ""),
            )
        )
        return hook_id

    def unregister(self, hook_id: str) -> bool:
        for records in self._hooks.values():
            for index, record in enumerate(records):
                if record.hook_id == hook_id:
                    del records[index]
                    return True
        return False

    def run(self, context: HookContext) -> list[HookResult]:
        hook_type = self._coerce_hook_type(context.hook_type)
        results: list[HookResult] = []
        for record in list(self._hooks[hook_type]):
            try:
                result = self._coerce_result(record.callback(context))
            except Exception as exc:
                result = HookResult.continue_(reason="hook_error")
                result.metadata["error"] = str(exc)
            self.invocations.append(
                {
                    "hook_id": record.hook_id,
                    "hook_name": record.name,
                    "hook_type": hook_type.value,
                    "decision": result.normalized_decision().value,
                    "reason": result.reason,
                }
            )
            results.append(result)
        return results

    def run_until_decision(self, context: HookContext) -> HookResult:
        for result in self.run(context):
            if result.normalized_decision() is not HookDecision.CONTINUE:
                return result
        return HookResult.continue_()

    def has_hooks(self, hook_type: HookType | str) -> bool:
        return bool(self._hooks[self._coerce_hook_type(hook_type)])

    def _coerce_hook_type(self, hook_type: HookType | str) -> HookType:
        if isinstance(hook_type, HookType):
            return hook_type
        return HookType(str(hook_type))

    def _coerce_result(
        self,
        result: HookResult | HookDecision | str | None,
    ) -> HookResult:
        if result is None:
            return HookResult.continue_()
        if isinstance(result, HookResult):
            return result
        return HookResult(decision=result)
