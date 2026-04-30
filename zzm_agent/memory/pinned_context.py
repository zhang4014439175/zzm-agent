from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from zzm_agent.constants import PROMPT_SECTION_PINNED_CONTEXT


_PATH_RE = re.compile(
    r"(?P<path>(?:[A-Za-z]:\\|\.{0,2}/|\.{0,2}\\)?[\w .\-\\/]+?\.[A-Za-z0-9_]{1,8})"
)


@dataclass
class PinnedContext:
    """Runtime context that should survive history compression."""

    user_goal: str = ""
    constraints: list[str] = field(default_factory=list)
    current_files: list[str] = field(default_factory=list)
    error_lines: list[str] = field(default_factory=list)
    open_plan: list[str] = field(default_factory=list)

    @classmethod
    def from_turn(
        cls,
        user_input: str,
        history: list[dict[str, Any]] | None = None,
    ) -> "PinnedContext":
        """Build pinned context from the current request plus recent history."""
        history = history or []
        context = cls(user_goal=" ".join(str(user_input).split())[:240])
        candidates = list(history[-12:]) + [{"role": "user", "content": user_input}]

        for message in candidates:
            text = " ".join(str(message.get("content", "")).split())
            if not text:
                continue
            context._collect_files(text)
            context._collect_constraints(text)
            context._collect_errors(text)
            context._collect_plan_items(text)

        return context

    def is_empty(self) -> bool:
        return not any(
            [
                self.constraints,
                self.current_files,
                self.error_lines,
                self.open_plan,
            ]
        )

    def to_message(self) -> dict[str, str] | None:
        """Render pinned context as a system message for model input."""
        if self.is_empty():
            return None

        lines = [f"[{PROMPT_SECTION_PINNED_CONTEXT}]"]
        if self.user_goal:
            lines.append(f"- User goal: {self.user_goal}")
        if self.constraints:
            lines.append("- Constraints: " + "; ".join(self.constraints[:5]))
        if self.current_files:
            lines.append("- Current files: " + ", ".join(self.current_files[:8]))
        if self.error_lines:
            lines.append("- Error lines: " + " | ".join(self.error_lines[:4]))
        if self.open_plan:
            lines.append("- Open plan: " + "; ".join(self.open_plan[:5]))
        return {"role": "system", "content": "\n".join(lines)}

    def _collect_files(self, text: str) -> None:
        for match in _PATH_RE.finditer(text):
            path = match.group("path").strip("`'\".,;:()[]{}")
            if path and path not in self.current_files:
                self.current_files.append(path)

    def _collect_constraints(self, text: str) -> None:
        lowered = text.lower()
        markers = (
            "must ",
            "must not",
            "don't ",
            "do not",
            "never ",
            "avoid ",
            "必须",
            "不要",
            "不能",
            "禁止",
            "保持",
        )
        if any(marker in lowered for marker in markers) or any(marker in text for marker in markers):
            excerpt = text[:180]
            if excerpt not in self.constraints:
                self.constraints.append(excerpt)

    def _collect_errors(self, text: str) -> None:
        lowered = text.lower()
        if not any(marker in lowered for marker in ("error", "traceback", "exception", "failed", "失败", "报错")):
            return
        excerpt = text[:220]
        if excerpt not in self.error_lines:
            self.error_lines.append(excerpt)

    def _collect_plan_items(self, text: str) -> None:
        lowered = text.lower()
        if not any(marker in lowered for marker in ("todo", "next", "plan", "待办", "下一步", "计划")):
            return
        excerpt = text[:180]
        if excerpt not in self.open_plan:
            self.open_plan.append(excerpt)
