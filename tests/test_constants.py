from pathlib import Path

from zzm_agent import constants


def test_prompt_section_constants_are_stable():
    assert constants.PROMPT_SECTION_ENVIRONMENT == "Environment"
    assert constants.PROMPT_SECTION_WORKING_MEMORY == "Working Memory"
    assert constants.PROMPT_SECTION_PINNED_CONTEXT == "Pinned Context"
    assert constants.PROMPT_SECTION_TOOL_GUIDE == "Tools"


def test_internal_paths_are_relative_to_agent_dir():
    assert constants.RULES_PATH == Path(".zzm_agent") / "rules.md"
    assert constants.PROJECT_STRUCTURE_PATH == (
        Path(".zzm_agent") / "index" / "project_structure.json"
    )
    assert constants.EVALUATIONS_PATH == (
        Path(".zzm_agent") / "evolution" / "evaluations.json"
    )
    assert constants.AUDIT_LOG_PATH == Path(".zzm_agent") / "audit.log"


def test_event_and_config_keys_are_namespaced():
    assert constants.EVENT_TOOL_START == "tool.start"
    assert constants.EVENT_TOOL_END == "tool.end"
    assert constants.EVENT_TOOL_ERROR == "tool.error"
    assert constants.CONFIG_AGENT_MAX_PARALLEL_TOOLS == "agent.max_parallel_tools"
