from __future__ import annotations

from pathlib import Path
from typing import Any

from zzm_agent.constants import ZZM_AGENT_DIR


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

    return Console(highlight=False)


def build_prompt_session(workspace: str | Path, runtime: dict[str, Any] | None = None):
    """Create an optional prompt_toolkit input session with history."""
    try:
        from prompt_toolkit import PromptSession
        from prompt_toolkit.history import FileHistory
        from prompt_toolkit.styles import Style
        from prompt_toolkit.formatted_text import HTML
        from prompt_toolkit.completion import WordCompleter
    except ImportError:
        return None

    history_path = Path(workspace) / ZZM_AGENT_DIR / "repl_history.txt"
    history_path.parent.mkdir(parents=True, exist_ok=True)
    style = Style.from_dict({
        "prompt": "ansicyan bold",
        "bottom-toolbar": "#ffffff bg:#333333",
    })
    
    completer = None
    bottom_toolbar = None
    
    if runtime:
        commands = [
            '/help', '/tools', '/reload', '/memory', '/sessions', 
            '/session', '/new', '/remember', '/forget', '/search', 
            '/semantic', '/evolve run', '/evolve diff', '/evolve apply', 
            '/evolve rollback', '/exit', '/quit'
        ]
        completer = WordCompleter(commands, ignore_case=True)
        
        loop = runtime.get("loop")
        store = runtime.get("store")
        
        def get_bottom_toolbar():
            if not loop or not store:
                return ""
            session_id = store.session_id
            model = loop.model
            token_count = loop.cumulative_usage.total_tokens
            
            return HTML(
                f' <b><style fg="ansigreen">[{session_id}]</style></b> '
                f'| <b>Model:</b> <style fg="ansiblue">{model}</style> '
                f'| <b>Tokens:</b> <style fg="ansiyellow">{token_count}</style> '
                f'| <i>输入 / 唤出指令菜单</i>'
            )
            
        bottom_toolbar = get_bottom_toolbar

    return PromptSession(
        history=FileHistory(str(history_path)),
        style=style,
        completer=completer,
        bottom_toolbar=bottom_toolbar,
        complete_while_typing=True,
        reserve_space_for_menu=4
    )


def read_repl_input(console: Any, prompt_session: Any | None) -> str:
    """Read one user input line, using prompt_toolkit when available."""
    if prompt_session is None:
        return console.input("[bold #61AFEF]you>[/bold #61AFEF] ").strip()
        
    try:
        from prompt_toolkit.formatted_text import HTML
        prompt_text = HTML('<style fg="ansicyan">you></style> ')
    except ImportError:
        prompt_text = [("class:prompt", "you> ")]
        
    return prompt_session.prompt(prompt_text).strip()


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


class MarkdownStreamRenderer:
    """
    Buffer streamed text and render complete Markdown blocks.

    Rich Markdown needs coherent blocks to style emphasis, lists, and code
    fences. Rendering every raw token chunk would expose syntax like
    ``**bold**`` while the sentence is still arriving.
    """

    def __init__(self, console: Any):
        self.console = console
        self._buffer = ""

    def push(self, chunk: str) -> None:
        """Add a streamed chunk and render any completed paragraph blocks."""
        self._buffer += chunk
        ready, remaining = self._split_ready_block(self._buffer)
        if not ready:
            return
        self._buffer = remaining
        render_reply(self.console, ready)

    def flush(self) -> None:
        """Render any remaining buffered text."""
        if not self._buffer:
            return
        render_reply(self.console, self._buffer)
        self._buffer = ""

    def _split_ready_block(self, text: str) -> tuple[str, str]:
        boundary = text.rfind("\n\n")
        if boundary == -1:
            return "", text
        split_at = boundary + 2
        return text[:split_at], text[split_at:]


def stream_reply_chunk(console: Any, chunk: str) -> None:
    """
    Render streamed chunks through a per-console Markdown buffer.

    Args:
        console: Console-like object used for output.
        chunk: Newly received text chunk.
    """
    renderer = getattr(console, "_zzm_markdown_stream_renderer", None)
    if renderer is None:
        renderer = MarkdownStreamRenderer(console)
        setattr(console, "_zzm_markdown_stream_renderer", renderer)
    renderer.push(chunk)


def render_help(console: Any) -> None:
    """
    Render a structured help message with available commands.
    """
    try:
        from rich import box
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
/evolve run   - Generate a prompt candidate
/evolve diff  - Show pending prompt candidate diff
/evolve apply - Apply the pending prompt candidate
/evolve rollback - Restore the previous prompt
/exit, /quit  - Terminate the session
        """
        console.print(help_text)
        return

    table = Table(show_header=True, header_style="bold #61AFEF", box=None, padding=(0, 1))
    table.add_column("Command", style="bold #56B6C2", no_wrap=True)
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
        ("/evolve run", "Generate a prompt candidate"),
        ("/evolve diff", "Show pending prompt candidate diff"),
        ("/evolve apply", "Apply the pending prompt candidate"),
        ("/evolve rollback", "Restore the previous prompt"),
        ("/exit, /quit", "Terminate the session"),
    ]

    for cmd, desc in commands:
        table.add_row(cmd, desc)

    console.print(
        Panel(
            table,
            title="[bold #61AFEF]zzm-agent Help[/bold #61AFEF]",
            title_align="left",
            border_style="#3B4252",
            box=box.ROUNDED,
            padding=(1, 2),
            expand=False,
        )
    )


def render_welcome(console: Any, session_id: str, model: str, workspace: str, tool_count: int) -> None:
    """
    Render a professional welcome panel with a logo and runtime information.
    """
    try:
        from rich import box
        from rich.panel import Panel
        from rich.table import Table
        from rich.align import Align
        from rich.text import Text
        from rich.console import Group
    except ImportError:
        console.print(f"[bold green]zzm-agent[/bold green] started (Session: {session_id})")
        return

    logo = Text("zzm-agent", style="bold #61AFEF")
    subtitle = Text("agentic coding console", style="dim #ABB2BF")
    
    # Info Table
    info_table = Table.grid(padding=(0, 2))
    info_table.add_column(style="dim #ABB2BF", justify="right")
    info_table.add_column(style="#DCDCAA")
    
    info_table.add_row("session", f"[#98C379]{session_id}[/]")
    info_table.add_row("model", f"[#61AFEF]{model}[/]")
    info_table.add_row("root", f"[#E5C07B]{workspace}[/]")
    info_table.add_row("tools", f"[#C678DD]{tool_count} registered[/]")

    # Group elements together for the panel
    welcome_group = Group(
        Align.center(logo),
        Align.center(subtitle),
        "",
        Align.center(info_table),
    )

    console.print(
        Panel(
            welcome_group,
            border_style="#3B4252",
            box=box.ROUNDED,
            padding=(1, 2),
            subtitle="[dim]Type /help for commands[/dim]",
            subtitle_align="center",
            expand=False,
        )
    )
