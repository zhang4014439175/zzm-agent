"""CLI input, completion, rendering, and theme components."""

from zzm_agent.cli_support.ui.completion import SlashCommandCompleter, SlashCommandLexer
from zzm_agent.cli_support.ui.input import build_bottom_toolbar, build_prompt_session, read_repl_input
from zzm_agent.cli_support.ui.renderers import (
    MarkdownStreamRenderer,
    PlainTextRenderer,
    TerminalRenderer,
    build_terminal_renderer,
    render_error_card,
    render_help,
    render_notification,
    render_reply,
    render_welcome,
)
from zzm_agent.cli_support.ui.theme import build_console

__all__ = [
    "MarkdownStreamRenderer", "PlainTextRenderer", "SlashCommandCompleter",
    "SlashCommandLexer", "TerminalRenderer", "build_bottom_toolbar",
    "build_console", "build_prompt_session", "build_terminal_renderer",
    "read_repl_input", "render_error_card", "render_help",
    "render_notification", "render_reply", "render_welcome",
]
