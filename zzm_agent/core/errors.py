from __future__ import annotations

import json
from dataclasses import asdict, dataclass


@dataclass
class ToolError:
    """Structured tool failure payload returned to the model."""

    error_type: str
    message: str
    recovery_hint: str
    retryable: bool = False

    def to_json(self) -> str:
        """Serialize the error so tool messages stay machine-readable."""
        return json.dumps(asdict(self), ensure_ascii=False, sort_keys=True)


class CommandTimeoutError(ToolError):
    """Structured error for commands or tools that exceed a time budget."""

    def __init__(self, message: str, recovery_hint: str | None = None):
        super().__init__(
            error_type="CommandTimeoutError",
            message=message,
            recovery_hint=recovery_hint
            or "Reduce the scope, increase the timeout, or use a background command.",
            retryable=True,
        )


def tool_error_from_exception(exc: Exception) -> ToolError:
    """Convert an unexpected tool exception into a recoverable model payload."""
    if isinstance(exc, TimeoutError):
        return CommandTimeoutError(str(exc))
    if isinstance(exc, json.JSONDecodeError):
        return ToolError(
            error_type="InvalidToolArguments",
            message=str(exc),
            recovery_hint="Regenerate the tool arguments as valid JSON matching the schema.",
            retryable=True,
        )
    if isinstance(exc, KeyError):
        return ToolError(
            error_type="ToolNotFound",
            message=str(exc),
            recovery_hint="Call one of the registered tools listed in the tool schema.",
            retryable=False,
        )
    if isinstance(exc, TypeError):
        return ToolError(
            error_type="ToolArgumentError",
            message=str(exc),
            recovery_hint="Check required parameters, names, and value types before retrying.",
            retryable=True,
        )
    return ToolError(
        error_type=exc.__class__.__name__,
        message=str(exc),
        recovery_hint="Inspect the error, adjust the plan or arguments, and retry only if useful.",
        retryable=False,
    )
