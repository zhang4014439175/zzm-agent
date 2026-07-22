"""Console construction and terminal theme compatibility."""

from zzm_agent.cli_support.ui.legacy import (
    _install_completion_menu_highlight_patch,
    _install_markdown_code_style_patch,
    _is_light_terminal_background,
    _windows_console_background_color,
    build_console,
)

__all__ = ["build_console"]
