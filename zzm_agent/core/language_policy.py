from __future__ import annotations

import locale
import os
import re
from dataclasses import dataclass
from typing import Any


SUPPORTED_RESPONSE_LANGUAGES = {"auto", "zh-CN", "en-US"}


@dataclass(frozen=True)
class ResponseLanguageDecision:
    """Resolved response language for one model turn."""

    language: str
    source: str
    confidence: float
    instruction: str


def resolve_response_language(
    user_input: str,
    *,
    previous_language: str | None = None,
    config: dict[str, Any] | None = None,
    default_language: str = "zh-CN",
) -> ResponseLanguageDecision:
    """Resolve the language the assistant should use for this turn."""
    ui_config = (config or {}).get("ui", {})
    configured = _normalize_language(ui_config.get("response_language"))
    default_from_config = _normalize_language(
        ui_config.get("default_locale_language")
    ) or default_language

    explicit = _detect_explicit_language(user_input)
    if explicit is not None:
        return _decision(explicit, "explicit", 1.0)

    detected = _detect_input_language(user_input)
    if detected is not None:
        return _decision(detected, "input_detected", 0.85)

    previous = _normalize_language(previous_language)
    if previous is not None and previous != "auto":
        return _decision(previous, "session", 0.75)

    if configured is not None and configured != "auto":
        return _decision(configured, "config", 0.7)

    system_language = _detect_system_language()
    if system_language is not None:
        return _decision(system_language, "system_locale", 0.55)

    return _decision(default_from_config, "default", 0.35)


def detect_system_response_language() -> str | None:
    """Return the response language inferred from OS locale/environment."""
    return _detect_system_language()


def _decision(language: str, source: str, confidence: float) -> ResponseLanguageDecision:
    normalized = _normalize_language(language) or "zh-CN"
    return ResponseLanguageDecision(
        language=normalized,
        source=source,
        confidence=confidence,
        instruction=_instruction_for_language(normalized),
    )


def _instruction_for_language(language: str) -> str:
    if language == "en-US":
        return (
            "Respond in English. Keep code, commands, file paths, API names, "
            "and diagnostics in their original language."
        )
    return (
        "请使用简体中文回答。代码、命令、文件路径、API 名称和诊断信息保持原文。"
    )


def _detect_explicit_language(text: str) -> str | None:
    lowered = text.lower()
    if re.search(r"(?:用|使用|请用|回答用|回复用)\s*(?:简体)?中文", text):
        return "zh-CN"
    if re.search(r"(?:用|使用|请用|回答用|回复用)\s*(?:英文|英语)", text):
        return "en-US"
    if re.search(r"\b(?:answer|respond|reply)\s+in\s+(?:english|en-us)\b", lowered):
        return "en-US"
    if re.search(r"\b(?:answer|respond|reply)\s+in\s+(?:chinese|zh-cn)\b", lowered):
        return "zh-CN"
    return None


def _detect_input_language(text: str) -> str | None:
    sample = _strip_non_language_content(text)
    if not sample:
        return None

    cjk_count = len(re.findall(r"[\u4e00-\u9fff]", sample))
    latin_count = len(re.findall(r"[A-Za-z]", sample))
    if cjk_count >= 2 and cjk_count >= max(1, latin_count // 2):
        return "zh-CN"
    if latin_count >= 8 and cjk_count == 0:
        words = re.findall(r"[A-Za-z]{2,}", sample)
        if len(words) >= 2:
            return "en-US"
    return None


def _strip_non_language_content(text: str) -> str:
    text = re.sub(r"```.*?```", " ", text, flags=re.DOTALL)
    text = re.sub(r"`[^`]+`", " ", text)
    text = re.sub(r"[A-Za-z]:[\\/][^\s]+", " ", text)
    text = re.sub(r"(?:\.{0,2}[\\/])?[\w.-]+(?:[\\/][\w.-]+)+", " ", text)
    text = re.sub(r"^/\w+(?:\s+[-\w./]+)*$", " ", text.strip())
    return text.strip()


def _detect_system_language() -> str | None:
    candidates: list[str] = []
    for key in ("LC_ALL", "LC_MESSAGES", "LANG", "LANGUAGE"):
        value = os.environ.get(key)
        if value:
            candidates.append(value)
    try:
        value = locale.getlocale()[0]
        if value:
            candidates.append(value)
    except Exception:
        pass
    for value in candidates:
        normalized = _normalize_language(value)
        if normalized is not None and normalized != "auto":
            return normalized
    return None


def _normalize_language(value: Any) -> str | None:
    if value is None:
        return None
    normalized = str(value).strip().replace("_", "-")
    if not normalized:
        return None
    lowered = normalized.lower()
    if lowered == "auto":
        return "auto"
    if lowered.startswith("zh"):
        return "zh-CN"
    if lowered.startswith("en"):
        return "en-US"
    return None
