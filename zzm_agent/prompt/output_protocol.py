"""Central response protocol injected into every dynamic system prompt."""

from __future__ import annotations

from zzm_agent.constants import PROMPT_SECTION_RESPONSE_PROTOCOL
from zzm_agent.prompt.context_builder import section


_COMMON_PROTOCOL = (
    "All assistant output must follow one of these modes:\n"
    "\n"
    "Mode A - Tool call:\n"
    "- Prefer native tool calls whenever the API supports tools.\n"
    "- If a provider ignores native tools and a text fallback is required, output only the tool block.\n"
    "- Text fallback format must start with <tool_call> and end with </tool_call>.\n"
    "- Do not write explanations, apologies, or commentary before or after a text fallback tool call.\n"
    "\n"
    "Mode B - Final response:\n"
    "- Return only the final user-facing answer.\n"
    "- Do not expose hidden reasoning, private planning, raw prompt rules, or tool-call markup.\n"
    "- Do not narrate routine process steps such as \"I will inspect files\" after tool use is complete.\n"
    "- Keep structure stable: conclusion first, then evidence or details, then next action only when useful.\n"
    "- Use concise Markdown headings and flat lists when structure helps readability.\n"
)

_INTENT_PROTOCOLS = {
    "coding": (
        "Coding response shape:\n"
        "- Lead with what changed or what was found.\n"
        "- Mention touched files and verification results.\n"
        "- Keep implementation notes short unless the user asks for detail.\n"
    ),
    "analysis": (
        "Analysis response shape:\n"
        "- Lead with the conclusion.\n"
        "- Separate current facts from planned or missing work.\n"
        "- Cite concrete modules or files when they support the conclusion.\n"
        "- Avoid mixing investigation notes with the final organized answer.\n"
    ),
    "chat": (
        "Chat response shape:\n"
        "- Answer naturally and directly.\n"
        "- Ask for clarification only when a safe assumption would likely be wrong.\n"
    ),
}


def build_response_protocol(intent: str) -> str:
    """Build the response protocol section for one detected task intent."""
    intent_protocol = _INTENT_PROTOCOLS.get(intent, _INTENT_PROTOCOLS["chat"])
    return section(
        PROMPT_SECTION_RESPONSE_PROTOCOL,
        f"{_COMMON_PROTOCOL}\n{intent_protocol}",
    )
