"""Slash-command completion and lexical highlighting."""

from zzm_agent.cli_support.ui.legacy import (
    PROMPT_COMPLETION_MENU_RESERVED_LINES,
    SlashCommandCompleter,
    SlashCommandLexer,
    _is_prefix_subsequence,
    _pin_completion_menu_position,
    _skill_name_matches,
    _skill_token_before_cursor,
    _slash_command_matches,
)

__all__ = [
    "PROMPT_COMPLETION_MENU_RESERVED_LINES",
    "SlashCommandCompleter",
    "SlashCommandLexer",
    "_skill_name_matches",
    "_skill_token_before_cursor",
]
