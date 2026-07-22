from __future__ import annotations

from zzm_agent.cli_support import commands, rendering
from zzm_agent.cli_support.commands import diagnostics, git, router, session
from zzm_agent.cli_support.commands.context import CommandContext
from zzm_agent.cli_support.ui import completion, input, renderers, theme


def test_slash_command_package_exposes_context_and_domain_modules() -> None:
    assert commands.handle_slash is router.handle_slash
    assert CommandContext.__module__ == "zzm_agent.cli_support.commands.context"
    assert callable(session._handle_resume)
    assert callable(git._handle_git)
    assert callable(diagnostics._handle_status)


def test_ui_package_preserves_renderer_and_input_identities() -> None:
    assert rendering.SlashCommandCompleter is completion.SlashCommandCompleter
    assert rendering.build_prompt_session is input.build_prompt_session
    assert rendering.TerminalRenderer is renderers.TerminalRenderer
    assert rendering.build_console is theme.build_console


def test_legacy_import_paths_remain_available() -> None:
    assert commands.handle_slash.__name__ == "handle_slash"
    assert rendering._plain_terminal_reply("hello") == "hello"

