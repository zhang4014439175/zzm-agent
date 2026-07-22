"""Structured terminal renderers."""

from zzm_agent.cli_support.ui.legacy import (
    MarkdownStreamRenderer,
    PlainTextRenderer,
    TerminalRenderer,
    _compact_reasoning_for_display,
    _is_code_like_line,
    _plain_terminal_reply,
    _strip_reply_emoji,
    build_terminal_renderer,
    render_error_card,
    render_help,
    render_notification,
    render_reply,
    render_welcome,
    stream_reply_chunk,
)

__all__ = [
    "MarkdownStreamRenderer", "PlainTextRenderer", "TerminalRenderer",
    "build_terminal_renderer", "render_error_card", "render_help",
    "render_notification", "render_reply", "render_welcome", "stream_reply_chunk",
]
