"""Context snippets used by the dynamic prompt manager."""

from __future__ import annotations

import os
import platform
from pathlib import Path
from typing import Any

from zzm_agent.constants import (
    PROMPT_SECTION_ENVIRONMENT,
    PROMPT_SECTION_PROJECT_RULES,
    PROMPT_SECTION_TOOL_GUIDE,
    RULES_PATH,
)


def section(title: str, body: str) -> str:
    """Render one prompt section when content exists."""
    cleaned = body.strip()
    if not cleaned:
        return ""
    return f"[{title}]\n{cleaned}"


def build_environment_context(workspace_root: str | Path | None = None) -> str:
    """Describe the local runtime environment for command and path choices."""
    workspace = Path(workspace_root or os.getcwd()).resolve()
    shell = _detect_shell()
    lines = [
        f"OS: {platform.system() or os.name}",
        f"Shell: {shell}",
        f"Workspace: {workspace}",
        f"Path separator: {os.sep}",
        f"Line ending: {_line_ending_name()}",
    ]
    if platform.system().lower().startswith("win") or "powershell" in shell.lower():
        lines.append(
            "Shell guidance: prefer PowerShell cmdlets and Windows paths when running commands."
        )
    else:
        lines.append("Shell guidance: prefer portable POSIX-style commands when appropriate.")
    return section(PROMPT_SECTION_ENVIRONMENT, "\n".join(lines))


def build_project_rules_context(workspace_root: str | Path | None = None) -> str:
    """Load project-level rules from .zzm_agent/rules.md when present."""
    workspace = Path(workspace_root or os.getcwd()).resolve()
    rules_file = workspace / RULES_PATH
    try:
        text = rules_file.read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        return ""
    except OSError as exc:
        text = f"Could not read {RULES_PATH}: {exc}"
    return section(PROMPT_SECTION_PROJECT_RULES, text)


def build_tool_guide_context(registry: Any | None) -> str:
    """Build a compact guide from registered tool schemas and metadata."""
    if registry is None:
        return ""

    tools = getattr(registry, "tools", {}) or {}
    lines: list[str] = []
    for name in sorted(tools):
        meta = tools[name]
        description = str(meta.get("description", "")).strip()
        risk = str(meta.get("risk_level", "low")).strip() or "low"
        group = str(meta.get("group", "")).strip()
        suffix = f", group={group}" if group else ""
        lines.append(f"- {name} ({risk}{suffix}): {description}")
        for example in meta.get("examples", []) or []:
            lines.append(f"  example: {example}")

    if not lines and hasattr(registry, "get_schemas"):
        try:
            schemas = registry.get_schemas()
        except Exception:
            schemas = []
        for schema in schemas:
            fn = schema.get("function", {}) if isinstance(schema, dict) else {}
            name = str(fn.get("name", "")).strip()
            description = str(fn.get("description", "")).strip()
            if name:
                lines.append(f"- {name}: {description}")

    return section(PROMPT_SECTION_TOOL_GUIDE, "\n".join(lines))


def _detect_shell() -> str:
    if os.environ.get("PSModulePath"):
        return "PowerShell"
    return (
        os.environ.get("SHELL")
        or os.environ.get("COMSPEC")
        or os.environ.get("ComSpec")
        or "unknown"
    )


def _line_ending_name() -> str:
    return "CRLF" if os.linesep == "\r\n" else "LF"
