from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Any


ERROR_ARGUMENT = "argument"
ERROR_PERMISSION = "permission"
ERROR_TIMEOUT = "timeout"
ERROR_ENVIRONMENT = "environment"
ERROR_EXTERNAL_SERVICE = "external_service"
ERROR_BUSINESS = "business"
ERROR_UNKNOWN = "unknown"


@dataclass
class ToolError:
    """Structured tool failure payload returned to the model."""

    error_type: str
    message: str
    recovery_hint: str
    retryable: bool = False
    category: str = ERROR_UNKNOWN
    deterministic: bool = True
    attempts: int = 1
    retry_after_seconds: float | None = None

    def to_json(self) -> str:
        """Serialize the error so tool messages stay machine-readable."""
        return json.dumps(asdict(self), ensure_ascii=False, sort_keys=True)

    def retry_summary(self) -> str:
        """Return a concise, model-facing summary of the retry decision."""
        if not self.retryable:
            if self.deterministic:
                return "This appears deterministic; do not repeat the same call without changing arguments or permissions."
            return "This is not safe to retry automatically; inspect the cause before trying a different approach."
        if self.retry_after_seconds is not None:
            return f"Retry only after about {self.retry_after_seconds:g} second(s), then change approach if it still fails."
        return "This may be transient; retry with bounded backoff, then change approach if it still fails."


class CommandTimeoutError(ToolError):
    """Structured error for commands or tools that exceed a time budget."""

    def __init__(self, message: str, recovery_hint: str | None = None):
        super().__init__(
            error_type="CommandTimeoutError",
            message=message,
            recovery_hint=recovery_hint
            or "Reduce the scope, increase the timeout, or use a background command.",
            retryable=True,
            category=ERROR_TIMEOUT,
            deterministic=False,
        )


def _coerce_retry_after(value: Any) -> float | None:
    """Parse Retry-After values from exception attributes or HTTP headers."""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return max(0.0, float(value))
    text = str(value).strip()
    if not text:
        return None
    try:
        return max(0.0, float(text))
    except ValueError:
        pass
    try:
        dt = parsedate_to_datetime(text)
    except (TypeError, ValueError, IndexError, OverflowError):
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return max(0.0, (dt - datetime.now(timezone.utc)).total_seconds())


def _extract_retry_after_seconds(exc: Exception) -> float | None:
    for attr in ("retry_after_seconds", "retry_after"):
        retry_after = _coerce_retry_after(getattr(exc, attr, None))
        if retry_after is not None:
            return retry_after

    response = getattr(exc, "response", None)
    headers = getattr(response, "headers", None)
    if headers:
        getter = getattr(headers, "get", None)
        if callable(getter):
            return _coerce_retry_after(getter("Retry-After") or getter("retry-after"))
    return None


def tool_error_from_exception(exc: Exception) -> ToolError:
    """Convert an unexpected tool exception into a recoverable model payload."""
    if isinstance(exc, TimeoutError):
        error = CommandTimeoutError(str(exc))
        if exc.__class__.__name__ == "ToolDeadlineExceeded":
            error.error_type = "ToolDeadlineExceeded"
            error.retryable = False
            error.deterministic = True
            error.recovery_hint = (
                "The synchronous tool exceeded its budget and stopped at the next safe checkpoint; "
                "reduce its scope or use a cancellable/background implementation."
            )
        return error
    if isinstance(exc, json.JSONDecodeError):
        return ToolError(
            error_type="InvalidToolArguments",
            message=str(exc),
            recovery_hint="Regenerate the tool arguments as valid JSON matching the schema.",
            retryable=False,
            category=ERROR_ARGUMENT,
            deterministic=True,
        )
    if isinstance(exc, KeyError):
        return ToolError(
            error_type="ToolNotFound",
            message=str(exc),
            recovery_hint="Call one of the registered tools listed in the tool schema.",
            retryable=False,
            category=ERROR_ARGUMENT,
            deterministic=True,
        )
    if exc.__class__.__name__ == "SandboxViolation":
        return ToolError(
            error_type="SandboxViolation",
            message=str(exc),
            recovery_hint="Request a controlled sandbox profile change or explicit escalation; never bypass the denied path or network boundary.",
            retryable=False,
            category=ERROR_PERMISSION,
            deterministic=True,
        )
    if isinstance(exc, PermissionError):
        return ToolError(
            error_type="ToolPermissionError",
            message=str(exc),
            recovery_hint="Request permission, choose a lower-risk tool, or ask the user to grant access.",
            retryable=False,
            category=ERROR_PERMISSION,
            deterministic=True,
        )
    if isinstance(exc, FileNotFoundError):
        return ToolError(
            error_type="FileNotFoundError",
            message=str(exc),
            recovery_hint="Verify the path, search for the file, or ask the user for the correct location.",
            retryable=False,
            category=ERROR_ENVIRONMENT,
            deterministic=True,
        )
    if exc.__class__.__name__ == "ToolArgumentValidationError":
        return ToolError(
            error_type="ToolArgumentValidationError",
            message=str(exc),
            recovery_hint="Check required parameters, names, and value types against the registered tool schema; do not request permission until they validate.",
            retryable=False,
            category=ERROR_ARGUMENT,
            deterministic=True,
        )
    if isinstance(exc, TypeError):
        return ToolError(
            error_type="ToolArgumentError",
            message=str(exc),
            recovery_hint="Check required parameters, names, and value types before retrying.",
            retryable=False,
            category=ERROR_ARGUMENT,
            deterministic=True,
        )
    if isinstance(exc, ConnectionError):
        retry_after = _extract_retry_after_seconds(exc)
        return ToolError(
            error_type=exc.__class__.__name__,
            message=str(exc),
            recovery_hint="The external dependency failed. Retry with bounded backoff, then report the blocker if it persists.",
            retryable=True,
            category=ERROR_EXTERNAL_SERVICE,
            deterministic=False,
            retry_after_seconds=retry_after,
        )
    if isinstance(exc, OSError):
        return ToolError(
            error_type=exc.__class__.__name__,
            message=str(exc),
            recovery_hint="Inspect the local environment, path, process, or filesystem state before retrying.",
            retryable=False,
            category=ERROR_ENVIRONMENT,
            deterministic=True,
        )
    return ToolError(
        error_type=exc.__class__.__name__,
        message=str(exc),
        recovery_hint="Inspect the error, adjust the plan or arguments, and retry only if useful.",
        retryable=False,
        category=ERROR_BUSINESS,
        deterministic=True,
    )
