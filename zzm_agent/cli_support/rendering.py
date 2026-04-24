from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any, Iterable

from zzm_agent.constants import ZZM_AGENT_DIR

_EMOJI_PATTERN = re.compile(
    "["
    "\U0001F1E6-\U0001F1FF"
    "\U0001F300-\U0001FAFF"
    "\U00002700-\U000027BF"
    "\U00002600-\U000026FF"
    "]+"
)

try:
    from rich.markdown import CodeBlock
except ImportError:
    CodeBlock = None


def _install_markdown_code_style_patch() -> None:
    if CodeBlock is None or getattr(CodeBlock, "_zzm_agent_no_background", False):
        return

    def render_code_block(self: Any, console: Any, options: Any) -> Any:
        from rich.syntax import Syntax

        code = str(self.text).rstrip()
        yield Syntax(
            code,
            self.lexer_name,
            theme="ansi_light",
            word_wrap=True,
            padding=0,
            background_color="default",
        )

    CodeBlock.__rich_console__ = render_code_block
    CodeBlock._zzm_agent_no_background = True

try:
    from prompt_toolkit.completion import Completer
except ImportError:
    Completer = object


def _install_completion_menu_highlight_patch() -> None:
    """Keep command and description columns highlighted as one selected row."""
    try:
        from prompt_toolkit.layout.containers import Float
        import prompt_toolkit.layout.menus as menus
    except ImportError:
        return

    if getattr(menus, "_zzm_agent_completion_patch", False):
        return

    original_item_fragments = menus._get_menu_item_fragments
    original_meta_fragments = menus.CompletionsMenuControl._get_menu_item_meta_fragments
    original_menu_width = menus.CompletionsMenuControl._get_menu_width

    def get_menu_item_fragments(
        completion: Any,
        is_current_completion: bool,
        width: int,
        space_after: bool = False,
    ) -> Any:
        if not is_current_completion:
            return original_item_fragments(completion, is_current_completion, width, space_after)

        text, text_width = menus._trim_formatted_text(
            completion.display,
            width - 2 if space_after else width - 1,
        )
        padding = " " * (width - 1 - text_width)
        return menus.to_formatted_text(
            [("", " ")] + text + [("", padding)],
            style="class:completion-menu.meta.completion.current",
        )

    def get_menu_item_meta_fragments(
        self: Any,
        completion: Any,
        is_current_completion: bool,
        width: int,
    ) -> Any:
        result = original_meta_fragments(self, completion, is_current_completion, width)
        if result and result[0][1] == " ":
            return result[1:]
        return result

    def get_menu_width(self: Any, max_width: int, complete_state: Any) -> int:
        return min(
            max_width,
            max(self.MIN_WIDTH, original_menu_width(self, max_width, complete_state) - 1),
        )

    menus._get_menu_item_fragments = get_menu_item_fragments
    menus.CompletionsMenuControl._get_menu_item_meta_fragments = get_menu_item_meta_fragments
    menus.CompletionsMenuControl._get_menu_width = get_menu_width

    original_float_init = Float.__init__

    def float_init(self: Any, *args: Any, **kwargs: Any) -> None:
        content = kwargs.get("content")
        if content is None and args:
            content = args[0]

        if isinstance(
            content,
            (menus.CompletionsMenu, menus.MultiColumnCompletionsMenu),
        ):
            kwargs["xcursor"] = None
            kwargs["left"] = 5

        original_float_init(self, *args, **kwargs)

    Float.__init__ = float_init
    menus._zzm_agent_completion_patch = True


def _is_light_terminal_background() -> bool:
    theme = os.environ.get("ZZM_AGENT_TERMINAL_THEME", "").strip().lower()
    if theme in {"light", "white"}:
        return True
    if theme in {"dark", "black"}:
        return False

    colorfgbg = os.environ.get("COLORFGBG", "")
    if ";" in colorfgbg:
        try:
            background = int(colorfgbg.rsplit(";", 1)[1])
        except ValueError:
            background = None
        if background is not None:
            return background in {7, 15}

    background = _windows_console_background_color()
    if background is not None:
        return background in {7, 15}

    return False


def _windows_console_background_color() -> int | None:
    if os.name != "nt":
        return None

    try:
        import ctypes
        from ctypes import wintypes
    except ImportError:
        return None

    class Coord(ctypes.Structure):
        _fields_ = [("X", wintypes.SHORT), ("Y", wintypes.SHORT)]

    class SmallRect(ctypes.Structure):
        _fields_ = [
            ("Left", wintypes.SHORT),
            ("Top", wintypes.SHORT),
            ("Right", wintypes.SHORT),
            ("Bottom", wintypes.SHORT),
        ]

    class ConsoleScreenBufferInfo(ctypes.Structure):
        _fields_ = [
            ("dwSize", Coord),
            ("dwCursorPosition", Coord),
            ("wAttributes", wintypes.WORD),
            ("srWindow", SmallRect),
            ("dwMaximumWindowSize", Coord),
        ]

    handle = ctypes.windll.kernel32.GetStdHandle(-11)
    if handle in (0, -1):
        return None

    info = ConsoleScreenBufferInfo()
    if not ctypes.windll.kernel32.GetConsoleScreenBufferInfo(handle, ctypes.byref(info)):
        return None

    return (info.wAttributes >> 4) & 0x0F


def _pin_completion_menu_position(prompt_session: Any, left: int = 5) -> None:
    """Keep the completion dropdown anchored after the prompt instead of the cursor."""
    try:
        from prompt_toolkit.layout.menus import (
            CompletionsMenu,
            MultiColumnCompletionsMenu,
        )
    except ImportError:
        return

    def pin_container(container: Any) -> None:
        for menu_float in getattr(container, "floats", ()):
            if isinstance(menu_float.content, (CompletionsMenu, MultiColumnCompletionsMenu)):
                menu_float.xcursor = None
                menu_float.left = left

        for child in getattr(container, "children", ()):
            pin_container(getattr(child, "content", child))

    pin_container(getattr(prompt_session.app.layout, "container", None))


class SlashCommandCompleter(Completer):
    """Prompt-toolkit completer that keeps slash command selection styling explicit."""

    def __init__(self, commands_meta: dict[str, str]) -> None:
        self._commands_meta = commands_meta

    def get_completions(self, document: Any, complete_event: Any) -> Iterable[Any]:
        text_before_cursor = document.text_before_cursor
        if not text_before_cursor.startswith("/"):
            return

        try:
            from prompt_toolkit.completion import Completion
        except ImportError as exc:
            raise RuntimeError("prompt_toolkit is required for slash completion.") from exc

        query = text_before_cursor.strip().lower()
        for command, description in self._commands_meta.items():
            if not _slash_command_matches(query, command):
                continue

            yield Completion(
                text=command,
                start_position=-len(text_before_cursor),
                display=command,
                display_meta=description,
            )


def _slash_command_matches(query: str, command: str) -> bool:
    if query == "/":
        return True

    compact_query = query.lstrip("/").replace(" ", "")
    compact_command = command.lower().lstrip("/").replace(" ", "")
    return _is_prefix_subsequence(compact_query, compact_command)


def _is_prefix_subsequence(needle: str, haystack: str) -> bool:
    if not needle:
        return True
    if not haystack or needle[0] != haystack[0]:
        return False

    iterator = iter(haystack)
    return all(char in iterator for char in needle)


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
        from rich.theme import Theme
    except ImportError as exc:
        raise RuntimeError("Rich is required to run the CLI interface.") from exc

    _install_markdown_code_style_patch()
    return Console(
        highlight=False,
        theme=Theme(
            {
                "markdown.h1": "bold",
                "markdown.h2": "bold",
                "markdown.h3": "bold",
                "markdown.h4": "bold",
                "markdown.strong": "bold",
                "markdown.em": "italic",
                "markdown.code": "#007777",
                "markdown.code_block": "#666666",
                "markdown.block_quote": "dim",
                "markdown.list": "none",
                "markdown.item.bullet": "none",
                "markdown.item.number": "none",
                "markdown.hr": "dim",
                "markdown.link": "none",
                "markdown.link_url": "dim underline",
            }
        ),
    )


def build_bottom_toolbar(runtime: dict[str, Any] | None = None):
    """Build the shared prompt-toolkit bottom toolbar."""
    if not runtime:
        return ""

    try:
        from prompt_toolkit.formatted_text import HTML
    except ImportError:
        return ""

    loop = runtime.get("loop")
    store = runtime.get("store")
    if not loop or not store:
        return ""

    workspace_path = os.environ.get("ZZM_AGENT_WORKSPACE_ROOT", os.getcwd())
    model = loop.model
    token_count = loop.cumulative_usage.total_tokens

    return HTML(
        f' <b>{workspace_path}</b> '
        f'| <b>Model:</b> {model} '
        f'| <b>Tokens:</b> {token_count} '
    )


def build_prompt_session(workspace: str | Path, runtime: dict[str, Any] | None = None):
    """Create an optional prompt_toolkit input session with history."""
    try:
        from prompt_toolkit import PromptSession
        from prompt_toolkit.history import FileHistory
        from prompt_toolkit.styles import Style
        from prompt_toolkit.formatted_text import HTML
    except ImportError:
        return None

    history_path = Path(workspace) / ZZM_AGENT_DIR / "repl_history.txt"
    history_path.parent.mkdir(parents=True, exist_ok=True)
    _install_completion_menu_highlight_patch()
    style = Style.from_dict({
        "prompt": "ansicyan bold",
        "bottom-toolbar": "noreverse bg:default fg:ansibrightblack",
        "bottom-toolbar.text": "noreverse bg:default fg:ansibrightblack",
        "completion-menu": "bg:default fg:default",
        "completion-menu.completion": "noreverse bg:default #666666",
        "completion-menu.completion.current": "noreverse bg:default fg:ansicyan bold",
        "completion-menu.meta.completion": "noreverse bg:default #8a8a8a",
        "completion-menu.meta.completion.current": "noreverse bg:default fg:ansicyan bold",
        "scrollbar.background": "bg:default",
        "scrollbar.button": "bg:default",
        "scrollbar.arrow": "bg:default",
    })
    
    completer = None
    bottom_toolbar = None
    
    if runtime:
        commands_meta = {
            "/help": "显示帮助信息",
            "/tools": "列出所有注册的工具",
            "/reload": "重新加载本地工具插件",
            "/memory": "显示最近历史和压缩状态",
            "/sessions": "列出所有已知的会话",
            "/session": "切换到指定的历史会话",
            "/new": "开启一轮全新的对话",
            "/remember": "添加一条长期的语义记忆",
            "/forget": "删除匹配关键字的长期记忆",
            "/search": "检索历史对话和长期记忆",
            "/semantic": "列出所有长期语义记忆",
            "/evolve run": "基于当前状态生成系统提示词",
            "/evolve diff": "查看新的提示词和旧版本的差异",
            "/evolve apply": "应用刚刚生成的新提示词",
            "/evolve rollback": "回滚到上一个提示词版本",
            "/exit": "退出当前会话",
            "/quit": "退出当前会话",
        }
        completer = SlashCommandCompleter(commands_meta)
        
        def get_bottom_toolbar():
            return build_bottom_toolbar(runtime)
            
        bottom_toolbar = get_bottom_toolbar

    prompt_session = PromptSession(
        history=FileHistory(str(history_path)),
        style=style,
        completer=completer,
        bottom_toolbar=bottom_toolbar,
        complete_while_typing=True,
        reserve_space_for_menu=10
    )
    _pin_completion_menu_position(prompt_session)
    return prompt_session


def read_repl_input(console: Any, prompt_session: Any | None) -> str:
    """Read one user input line, using prompt_toolkit when available."""
    if prompt_session is None:
        return console.input("[bold #61AFEF]>[/bold #61AFEF] ").strip()
        
    try:
        from prompt_toolkit.formatted_text import HTML
        prompt_text = HTML('<style fg="ansicyan">></style> ')
    except ImportError:
        prompt_text = [("class:prompt", "> ")]
        
    return prompt_session.prompt(prompt_text).strip()


def render_reply(console: Any, reply: str) -> None:
    """
    Render an assistant reply using Rich Markdown when available.

    Args:
        console: Console-like object used for output.
        reply: Final assistant reply text to render.
    """
    try:
        from rich.text import Text
    except ImportError:
        console.print(_plain_terminal_reply(reply), markup=False, highlight=False)
        return

    text = _plain_terminal_reply(reply)
    styled = Text()
    for line in text.splitlines():
        style = "default"
        if _is_code_like_line(line):
            style = "#7f8790"
        styled.append(line, style=style)
        styled.append("\n")
    styled.rstrip()
    console.print(styled, markup=False, highlight=False)


def _strip_reply_emoji(reply: str) -> str:
    """Remove emoji glyphs from assistant output for a cleaner terminal display."""
    return _EMOJI_PATTERN.sub("", reply)


def _plain_terminal_reply(reply: str) -> str:
    """Render assistant Markdown as quiet terminal text."""
    text = _strip_reply_emoji(reply)
    cleaned: list[str] = []
    in_fence = False
    need_bullet = True

    for raw_line in text.splitlines():
        line = raw_line.rstrip()
        stripped = line.strip()

        if stripped.startswith("```"):
            in_fence = not in_fence
            continue
        if not in_fence and stripped in {"---", "***", "___"}:
            continue
        if not in_fence:
            heading_match = re.match(r"^\s{0,3}#{1,6}\s*(.+?)\s*$", line)
            if heading_match is not None:
                line = f"\u2022{heading_match.group(1)}"
            line = re.sub(r"\*\*(.*?)\*\*", r"\1", line)
            line = re.sub(r"__(.*?)__", r"\1", line)
            line = re.sub(r"`([^`]+)`", r"\1", line)
            line = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", r"\1 (\2)", line)
        is_code_line = _is_code_like_line(line)
        if line and not is_code_line and line.lstrip().startswith("\u2022"):
            need_bullet = False
        elif line and not is_code_line and not line.lstrip().startswith("\u2022"):
            if need_bullet:
                line = f"\u2022{line.lstrip()}"
            else:
                line = line.lstrip()
            need_bullet = False
        elif is_code_line:
            need_bullet = True

        cleaned.append(line)

    return "\n".join(cleaned).rstrip()


def _is_code_like_line(line: str) -> bool:
    stripped = line.strip()
    if not stripped:
        return False
    if stripped.startswith(
        (
            "\u2502",
            "\u251c",
            "\u2514",
            "\u250c",
            "\u2510",
            "\u2518",
            "\u252c",
            "\u2534",
            "\u253c",
            ":",
        )
    ):
        return True
    if "/" in stripped and ("#" in stripped or stripped.startswith(("-", "*"))):
        return True
    if line.startswith(("    ", "\t")) or stripped.startswith("```"):
        return True
    return False


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
        """Add a streamed chunk to the buffer."""
        self._buffer += chunk
        ready, rest = self._split_ready_block(self._buffer)
        if not ready:
            return

        render_reply(self.console, ready)
        self._buffer = rest

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

    logo = Text("zzm-agent", style="bold #56B6C2")
    subtitle = Text("agentic coding console", style="dim #ABB2BF")
    
    # Info Table
    info_table = Table.grid(padding=(0, 2))
    info_table.add_column(style="dim #ABB2BF", justify="right")
    info_table.add_column(style="#DCDCAA")
    
    info_table.add_row("session", f"[#98C379]{session_id}[/]")
    info_table.add_row("model", f"[#56B6C2]{model}[/]")
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

