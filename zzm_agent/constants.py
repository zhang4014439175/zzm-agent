"""Shared constants for zzm-agent.

Keep cross-module names here so prompt sections, internal paths, event names,
and config keys do not drift as the agent grows.
"""

from __future__ import annotations

from pathlib import Path


# Prompt section labels.
PROMPT_SECTION_ENVIRONMENT = "Environment"
PROMPT_SECTION_WORKING_MEMORY = "Working Memory"
PROMPT_SECTION_PINNED_CONTEXT = "Pinned Context"
PROMPT_SECTION_TOOL_GUIDE = "Tools"


# Internal workspace metadata paths.
ZZM_AGENT_DIR = ".zzm_agent"
RULES_FILENAME = "rules.md"
INDEX_DIR = "index"
PROJECT_STRUCTURE_FILENAME = "project_structure.json"
EVOLUTION_DIR = "evolution"
EVALUATIONS_FILENAME = "evaluations.json"
EVOLUTION_CANDIDATES_FILENAME = "candidates.json"
PROMPT_HISTORY_FILENAME = "prompt_history.json"
ACTIVE_PROMPT_FILENAME = "active_prompt.json"
AUDIT_LOG_FILENAME = "audit.log"

RULES_PATH = Path(ZZM_AGENT_DIR) / RULES_FILENAME
PROJECT_STRUCTURE_PATH = Path(ZZM_AGENT_DIR) / INDEX_DIR / PROJECT_STRUCTURE_FILENAME
EVALUATIONS_PATH = Path(ZZM_AGENT_DIR) / EVOLUTION_DIR / EVALUATIONS_FILENAME
EVOLUTION_CANDIDATES_PATH = (
    Path(ZZM_AGENT_DIR) / EVOLUTION_DIR / EVOLUTION_CANDIDATES_FILENAME
)
PROMPT_HISTORY_PATH = Path(ZZM_AGENT_DIR) / EVOLUTION_DIR / PROMPT_HISTORY_FILENAME
ACTIVE_PROMPT_PATH = Path(ZZM_AGENT_DIR) / EVOLUTION_DIR / ACTIVE_PROMPT_FILENAME
AUDIT_LOG_PATH = Path(ZZM_AGENT_DIR) / AUDIT_LOG_FILENAME


# Event names.
EVENT_TOOL_START = "tool.start"
EVENT_TOOL_END = "tool.end"
EVENT_TOOL_ERROR = "tool.error"


# Config keys.
CONFIG_AGENT_MAX_PARALLEL_TOOLS = "agent.max_parallel_tools"
