from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Iterable


@dataclass(frozen=True)
class ToolObservation:
    """One completed tool call as seen by the progress monitor."""

    tool_name: str
    arguments: str
    content: str
    success: bool
    retryable: bool = False


@dataclass(frozen=True)
class ProgressSignal:
    """Structured indication that recent tool rounds are not making progress."""

    reason: str
    round_count: int
    detail: str


class ProgressMonitor:
    """Detect repeated outcomes, deterministic failures, and tool-call cycles."""

    def __init__(
        self,
        *,
        repeated_observation_limit: int = 3,
        non_retryable_failure_limit: int = 2,
        cycle_repetition_limit: int = 2,
        max_cycle_length: int = 3,
    ) -> None:
        self.repeated_observation_limit = max(2, repeated_observation_limit)
        self.non_retryable_failure_limit = max(2, non_retryable_failure_limit)
        self.cycle_repetition_limit = max(2, cycle_repetition_limit)
        self.max_cycle_length = max(2, max_cycle_length)
        self._round_fingerprints: list[tuple[str, ...]] = []
        self._last_observation_fingerprint: tuple[str, ...] | None = None
        self._repeated_observation_count = 0
        self._non_retryable_failure_count = 0

    def observe_round(
        self,
        observations: Iterable[ToolObservation],
    ) -> ProgressSignal | None:
        """Record one completed tool round and return a stall signal when detected."""
        completed = tuple(observations)
        if not completed:
            return None

        round_fingerprint = tuple(self._call_fingerprint(item) for item in completed)
        observation_fingerprint = tuple(
            self._observation_fingerprint(item) for item in completed
        )
        self._round_fingerprints.append(round_fingerprint)
        round_count = len(self._round_fingerprints)

        cycle_length = self._repeating_cycle_length()
        if cycle_length is not None:
            return ProgressSignal(
                reason="repeating_tool_cycle",
                round_count=round_count,
                detail=(
                    "Recent tool rounds repeat a fixed cycle "
                    f"of length {cycle_length} without new observations."
                ),
            )

        if all(not item.success and not item.retryable for item in completed):
            self._non_retryable_failure_count += 1
        else:
            self._non_retryable_failure_count = 0

        if self._non_retryable_failure_count >= self.non_retryable_failure_limit:
            return ProgressSignal(
                reason="consecutive_non_retryable_failures",
                round_count=round_count,
                detail=(
                    "Consecutive tool rounds ended only in non-retryable failures."
                ),
            )

        if observation_fingerprint == self._last_observation_fingerprint:
            self._repeated_observation_count += 1
        else:
            self._last_observation_fingerprint = observation_fingerprint
            self._repeated_observation_count = 1

        if self._repeated_observation_count >= self.repeated_observation_limit:
            return ProgressSignal(
                reason="repeated_observation",
                round_count=round_count,
                detail=(
                    "Different attempts produced the same normalized tool "
                    "observation repeatedly."
                ),
            )

        return None

    def _repeating_cycle_length(self) -> int | None:
        history = self._round_fingerprints
        for cycle_length in range(2, self.max_cycle_length + 1):
            required = cycle_length * self.cycle_repetition_limit
            if len(history) < required:
                continue
            pattern = history[-cycle_length:]
            start = len(history) - required
            if all(
                history[start + offset : start + offset + cycle_length]
                == pattern
                for offset in range(0, required, cycle_length)
            ):
                return cycle_length
        return None

    def _call_fingerprint(self, observation: ToolObservation) -> str:
        payload = {
            "tool_name": observation.tool_name,
            "arguments": self._normalize_json_or_text(observation.arguments),
            "observation": self._normalize_json_or_text(observation.content),
            "success": observation.success,
            "retryable": observation.retryable,
        }
        return self._digest(payload)

    def _observation_fingerprint(self, observation: ToolObservation) -> str:
        payload = {
            "tool_name": observation.tool_name,
            "observation": self._normalize_json_or_text(observation.content),
            "success": observation.success,
            "retryable": observation.retryable,
        }
        return self._digest(payload)

    def _normalize_json_or_text(self, value: str) -> str:
        text = str(value).strip()
        try:
            parsed = json.loads(text)
        except (json.JSONDecodeError, TypeError):
            return " ".join(text.split())
        return json.dumps(parsed, ensure_ascii=False, sort_keys=True, separators=(",", ":"))

    def _digest(self, payload: dict[str, object]) -> str:
        serialized = json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        return hashlib.sha256(serialized.encode("utf-8")).hexdigest()
