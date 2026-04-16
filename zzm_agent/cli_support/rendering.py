from __future__ import annotations

from typing import Any


def build_console():
    """
    Create a Rich console instance for interactive output.

    Returns:
        An initialized ``rich.console.Console`` instance.

    Raises:
        RuntimeError: If Rich is not installed in the current interpreter.
    """
    try:
        from rich.console import Console
    except ImportError as exc:
        raise RuntimeError("Rich is required to run the CLI interface.") from exc

    return Console()


def render_reply(console: Any, reply: str) -> None:
    """
    Render an assistant reply using Rich Markdown when available.

    Args:
        console: Console-like object used for output.
        reply: Final assistant reply text to render.
    """
    try:
        from rich.markdown import Markdown
    except ImportError:
        console.print(reply)
        return

    console.print(Markdown(reply))


def stream_reply_chunk(console: Any, chunk: str) -> None:
    """
    Render streamed plain-text chunks as they arrive.

    Args:
        console: Console-like object used for output.
        chunk: Newly received text chunk.
    """
    console.print(chunk, end="")
