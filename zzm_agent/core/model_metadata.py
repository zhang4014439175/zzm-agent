from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any
from urllib.parse import urljoin
from urllib.request import Request, urlopen


@dataclass(frozen=True)
class ModelContextLimit:
    """Resolved context-window limit for the active model."""

    tokens: int
    source: str


def resolve_model_context_limit(
    cfg: dict[str, Any],
    *,
    timeout_seconds: float = 2.5,
) -> ModelContextLimit:
    """Resolve the model context window from config, provider metadata, or memory fallback."""
    model_cfg = cfg.get("model", {})
    memory_cfg = cfg.get("memory", {})
    configured = _first_int(
        model_cfg.get("context_window_tokens"),
        model_cfg.get("context_length"),
    )
    if configured is not None:
        return ModelContextLimit(tokens=configured, source="config")

    base_url = str(model_cfg.get("base_url", "") or "")
    model_name = str(model_cfg.get("model_name", "") or "")
    api_key = str(model_cfg.get("api_key", "") or "")
    if _looks_like_openrouter(base_url) and model_name:
        discovered = _fetch_openrouter_context_limit(
            base_url=base_url,
            api_key=api_key,
            model_name=model_name,
            timeout_seconds=timeout_seconds,
        )
        if discovered is not None:
            return ModelContextLimit(tokens=discovered, source="openrouter")

    fallback = _first_int(memory_cfg.get("max_context_tokens")) or 32000
    return ModelContextLimit(tokens=fallback, source="memory")


def _looks_like_openrouter(base_url: str) -> bool:
    return "openrouter.ai" in base_url.lower()


def _fetch_openrouter_context_limit(
    *,
    base_url: str,
    api_key: str,
    model_name: str,
    timeout_seconds: float,
) -> int | None:
    models_url = urljoin(base_url.rstrip("/") + "/", "models")
    headers = {"Accept": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    request = Request(models_url, headers=headers)

    try:
        with urlopen(request, timeout=timeout_seconds) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except Exception:
        return None

    for item in payload.get("data", []):
        if not isinstance(item, dict) or item.get("id") != model_name:
            continue
        return _first_int(
            item.get("context_length"),
            (item.get("top_provider") or {}).get("context_length")
            if isinstance(item.get("top_provider"), dict)
            else None,
        )
    return None


def _first_int(*values: Any) -> int | None:
    for value in values:
        if value is None or value == "":
            continue
        try:
            parsed = int(value)
        except (TypeError, ValueError):
            continue
        if parsed > 0:
            return parsed
    return None
