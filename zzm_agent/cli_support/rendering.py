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


def render_help(console: Any) -> None:
    """
    Render a structured help message with available commands.
    """
    try:
        from rich.table import Table
        from rich.panel import Panel
    except ImportError:
        help_text = """
Available Commands:
/help         - Show this help message
/tools        - List all registered tools
/reload       - Reload plugin tools from disk
/memory       - Show recent conversation history and compression state
/sessions     - List all known conversation sessions
/session <id> - Switch to a specific session
/new          - Start a clean conversation session
/remember <f> - Add a long-term semantic memory fact
/forget <k>   - Remove long-term memories matching a keyword
/search <k>   - Search across semantic and episodic memories
/semantic     - List all long-term semantic memories
/evolve       - Run prompt optimization based on session history
/exit, /quit  - Terminate the session
        """
        console.print(help_text)
        return

    table = Table(show_header=True, header_style="bold blue", box=None)
    table.add_column("Command", style="cyan", no_wrap=True)
    table.add_column("Description", style="white")

    commands = [
        ("/help", "Show this help message"),
        ("/tools", "List all registered tools"),
        ("/reload", "Reload plugin tools from disk"),
        ("/memory", "Show recent history and compression state"),
        ("/sessions", "List all known conversation sessions"),
        ("/session <id>", "Switch to a specific session"),
        ("/new", "Start a clean conversation session"),
        ("/remember <f>", "Add a long-term semantic memory fact"),
        ("/forget <k>", "Remove memories matching a keyword"),
        ("/search <k>", "Search semantic and episodic memories"),
        ("/semantic", "List all long-term semantic memories"),
        ("/evolve", "Run prompt optimization based on history"),
        ("/exit, /quit", "Terminate the session"),
    ]

    for cmd, desc in commands:
        table.add_row(cmd, desc)

    console.print(
        Panel(
            table,
            title="[bold blue]zzm-agent Help[/bold blue]",
            title_align="left",
            border_style="blue",
            expand=False
        )
    )


def render_welcome(console: Any, session_id: str, model: str, workspace: str, tool_count: int) -> None:
    """
    Render a professional welcome panel with a logo and runtime information.
    """
    try:
        from rich.panel import Panel
        from rich.table import Table
        from rich.align import Align
        from rich.text import Text
        from rich.console import Group
    except ImportError:
        console.print(f"[bold green]zzm-agent[/bold green] started (Session: {session_id})")
        return

    # ASCII Logo
    logo_art = r"""
 ______ ______ __  __     ______  ______ ______ __   __ ______ 
/\___  /\___  /\ \/\ \   /\  __ \/\  ___\/\  ___\/\ "-.\ /\__  _\
\/_/  /\/ /  /\ \ \_\ \  \ \  __ \ \ \__ \ \  __\\ \ \-.  \/_/\ \/
  /\_____/\____\ \_____\  \ \_\ \_\ \_____\ \_____\ \_\\"\_\  \ \_\
  \/_____\/____/\/_____/   \/_/\/_/\/_____/\/_____/\/_/ \/_/   \/_/
    """
    
    logo = Text(logo_art, style="bold cyan")
    
    # Info Table
    info_table = Table.grid(padding=(0, 1))
    info_table.add_column(style="bold white", justify="right")
    info_table.add_column(style="cyan")
    
    info_table.add_row("Session:", f"[green]{session_id}[/green]")
    info_table.add_row("Model:", f"[blue]{model}[/blue]")
    info_table.add_row("Root:", f"[yellow]{workspace}[/yellow]")
    info_table.add_row("Tools:", f"[magenta]{tool_count} registered[/magenta]")
    info_table.add_row()

    # Group elements together for the panel
    welcome_group = Group(
        Align.center(logo),
        Align.center(info_table)
    )

    console.print(
        Panel(
            welcome_group,
            border_style="blue",
            subtitle="[dim]Type /help for commands[/dim]",
            subtitle_align="center",
            expand=False
        )
    )
