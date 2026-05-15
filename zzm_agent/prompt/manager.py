"""Dynamic system prompt assembly."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from zzm_agent.constants import PROMPT_SECTION_OUTPUT_FORMAT
from zzm_agent.prompt.context_builder import (
    build_environment_context,
    build_project_rules_context,
    build_tool_guide_context,
    section,
)
from zzm_agent.prompt.templates import get_template

_CODING_KEYWORDS = (
    "代码",
    "实现",
    "修复",
    "bug",
    "test",
    "pytest",
    "重构",
    "文件",
    "函数",
    "class",
    "def ",
    "cli",
    "api",
)
_ANALYSIS_KEYWORDS = (
    "分析",
    "查看",
    "review",
    "评估",
    "判断",
    "进度",
    "状态",
    "哪里",
    "哪一步",
)
_PATH_PATTERN = re.compile(r"(^|\s)([\w.-]+[\\/])+[\w.-]+")


def detect_intent(user_input: str, history: list[dict[str, Any]] | None = None) -> str:
    """Classify the current turn into coding, analysis, or chat."""
    text = (user_input or "").lower()
    recent = " ".join(
        str(message.get("content", ""))
        for message in (history or [])[-4:]
        if isinstance(message, dict)
    ).lower()
    combined = f"{text}\n{recent}"

    if _PATH_PATTERN.search(user_input) or any(word in combined for word in _CODING_KEYWORDS):
        return "coding"
    if any(word in combined for word in _ANALYSIS_KEYWORDS):
        return "analysis"
    return "chat"


class PromptManager:
    """Compose a system prompt from intent, project context, tools, and runtime facts."""

    def __init__(
        self,
        *,
        base_prompt: str = "",
        workspace_root: str | Path | None = None,
        registry: Any | None = None,
        include_tool_guide: bool = True,
    ) -> None:
        self.base_prompt = base_prompt.strip()
        self.workspace_root = Path(workspace_root).resolve() if workspace_root else None
        self.registry = registry
        self.include_tool_guide = include_tool_guide

    def build(
        self,
        user_input: str,
        history: list[dict[str, Any]] | None = None,
    ) -> str:
        """Return the final system prompt for one user turn."""
        intent = detect_intent(user_input, history)
        template = get_template(intent)
        parts = [
            template.role,
            self.base_prompt,
            self._rules_block(template.rules),
            build_project_rules_context(self.workspace_root),
            build_environment_context(self.workspace_root),
        ]
        if self.include_tool_guide:
            parts.append(build_tool_guide_context(self.registry))
        parts.append(section(PROMPT_SECTION_OUTPUT_FORMAT, template.output_format))
        return "\n\n".join(part for part in parts if part.strip())

    def _rules_block(self, rules: tuple[str, ...]) -> str:
        if not rules:
            return ""
        return "Rules:\n- " + "\n- ".join(rules)
