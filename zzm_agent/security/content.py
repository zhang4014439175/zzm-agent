from __future__ import annotations

import re
from enum import Enum
from typing import Any


REDACTED = "[REDACTED]"


class ContentTrust(str, Enum):
    """Trust assigned to content before it enters logs or model context."""

    TRUSTED = "trusted"
    UNTRUSTED = "untrusted"
    UNKNOWN = "unknown"


_SENSITIVE_KEYS = {
    "api_key",
    "apikey",
    "access_token",
    "auth_token",
    "authorization",
    "client_secret",
    "password",
    "private_key",
    "refresh_token",
    "secret",
    "token",
}
_TEXT_PATTERNS = (
    re.compile(r"(?i)\b(bearer\s+)[a-z0-9._~+/-]{8,}"),
    re.compile(
        r"(?i)\b(api[_-]?key|access[_-]?token|auth[_-]?token|client[_-]?secret|"
        r"password|refresh[_-]?token|secret|token)\b(\s*[:=]\s*)"
        r"([^\s,;]+)"
    ),
    re.compile(r"\b(sk-[A-Za-z0-9_-]{12,})\b"),
    re.compile(
        r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----.*?"
        r"-----END (?:RSA |EC |OPENSSH )?PRIVATE KEY-----",
        re.DOTALL,
    ),
)


def _is_sensitive_key(key: Any) -> bool:
    normalized = str(key).strip().lower().replace("-", "_")
    return normalized in _SENSITIVE_KEYS or normalized.endswith(("_secret", "_token", "_password"))


def redact_text(value: str) -> str:
    """Mask common credentials while preserving useful surrounding context."""
    text = value
    text = _TEXT_PATTERNS[0].sub(lambda match: f"{match.group(1)}{REDACTED}", text)
    text = _TEXT_PATTERNS[1].sub(
        lambda match: f"{match.group(1)}{match.group(2)}{REDACTED}",
        text,
    )
    text = _TEXT_PATTERNS[2].sub(REDACTED, text)
    text = _TEXT_PATTERNS[3].sub(REDACTED, text)
    return text


def redact_secrets(value: Any, *, key: str | None = None) -> Any:
    """Recursively redact secrets from JSON-like values."""
    if key is not None and _is_sensitive_key(key):
        return REDACTED
    if isinstance(value, dict):
        return {
            str(item_key): redact_secrets(item_value, key=str(item_key))
            for item_key, item_value in value.items()
        }
    if isinstance(value, list):
        return [redact_secrets(item) for item in value]
    if isinstance(value, tuple):
        return tuple(redact_secrets(item) for item in value)
    if isinstance(value, str):
        return redact_text(value)
    return value


def trust_metadata(
    *,
    source: str,
    trust: ContentTrust | str = ContentTrust.UNTRUSTED,
) -> dict[str, str]:
    """Create stable source and trust metadata for externally supplied content."""
    trust_value = trust.value if isinstance(trust, ContentTrust) else ContentTrust(trust).value
    return {"content_source": str(source), "content_trust": trust_value}
