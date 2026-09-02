from __future__ import annotations

import os
import re
import sys
from pathlib import Path
from typing import Any, Iterable

if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
if hasattr(sys.stderr, "reconfigure"):
    try:
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

from zzm_agent.constants import ZZM_AGENT_DIR
from zzm_agent.core.local_tool_renderers import (
    build_local_tool_renderer_registry,
    parse_tool_arguments,
)
from zzm_agent.core.model_stream import ModelStreamEvent, ModelStreamEventKind
from zzm_agent.core.tool_results import ToolRenderContext, ToolResult

_EMOJI_PATTERN = re.compile(
    "["
    "\U0001F1E6-\U0001F1FF"
    "\U0001F300-\U0001FAFF"
    "\U00002700-\U000027BF"
    "\U00002600-\U000026FF"
    "]+"
)
PROMPT_COMPLETION_MENU_RESERVED_LINES = 1
MAX_VISIBLE_REASONING_CHARS = 240

try:
    from rich.markdown import CodeBlock, Heading, Markdown, MarkdownElement, TextElement
except ImportError:
    CodeBlock = None
    Heading = None
    Markdown = None
    MarkdownElement = object
    TextElement = object


class TableCellElement(TextElement):
    """A cell in a markdown table (th or td)."""

    @classmethod
    def create(cls, markdown: Any, token: Any) -> TableCellElement:
        return cls(is_header=(getattr(token, "type", "") == "th_open"))

    def __init__(self, is_header: bool = False) -> None:
        super().__init__()
        self.is_header = is_header


class TableRowElement(MarkdownElement):
    """A row in a markdown table."""

    def __init__(self) -> None:
        self.cells: list[TableCellElement] = []

    def on_child_close(self, context: Any, child: MarkdownElement) -> bool:
        if isinstance(child, TableCellElement):
            self.cells.append(child)
        return False


class TableSectionElement(MarkdownElement):
    """A thead or tbody in a markdown table."""

    def __init__(self) -> None:
        self.rows: list[TableRowElement] = []

    def on_child_close(self, context: Any, child: MarkdownElement) -> bool:
        if isinstance(child, TableRowElement):
            self.rows.append(child)
        return False


class TableElement(MarkdownElement):
    """A table element."""

    new_line: bool = True

    def __init__(self) -> None:
        self.sections: list[TableSectionElement] = []
        self.rows: list[TableRowElement] = []

    def on_child_close(self, context: Any, child: MarkdownElement) -> bool:
        if isinstance(child, TableSectionElement):
            self.sections.append(child)
        elif isinstance(child, TableRowElement):
            self.rows.append(child)
        return False

    def __rich_console__(self, console: Any, options: Any) -> Any:
        from rich import box
        from rich.table import Table

        headers: list[str] = []
        body: list[list[str]] = []
        for section in self.sections:
            for row in section.rows:
                texts = [cell.text.plain for cell in row.cells]
                if any(getattr(cell, "is_header", False) for cell in row.cells):
                    headers = texts
                else:
                    body.append(texts)
        for row in self.rows:
            texts = [cell.text.plain for cell in row.cells]
            if any(getattr(cell, "is_header", False) for cell in row.cells):
                headers = texts
            else:
                body.append(texts)
        num_cols = max(len(headers), max((len(r) for r in body), default=0))
        if not num_cols:
            return

        is_light = _is_light_terminal_background()
        header_style = "bold #0969DA" if is_light else "bold #61AFEF"
        border_style = "dim #D0D7DE" if is_light else "dim #3B4252"

        table = Table(
            box=box.ROUNDED,
            show_header=bool(headers),
            header_style=header_style,
            border_style=border_style,
            padding=(0, 1),
        )
        if headers:
            for header in headers:
                table.add_column(header)
        else:
            for _ in range(num_cols):
                table.add_column()
        for row_cells in body:
            while len(row_cells) < num_cols:
                row_cells.append("")
            table.add_row(*row_cells[:num_cols])
        yield table


try:
    from pygments.style import Style as PygmentsStyle
    from pygments.token import (
        Comment,
        Error,
        Generic,
        Keyword,
        Name,
        Number,
        Operator,
        Punctuation,
        String,
        Text,
        Token,
    )

    class ZzmLightCodeStyle(PygmentsStyle):
        """High contrast, pitch-black base syntax style for light/white terminals."""

        default_style = "#000000"
        background_color = "#FFFFFF"
        highlight_color = "#E8EAEC"
        styles = {
            Token: "#000000",
            Text: "#000000",
            Comment: "italic #6E7781",
            Comment.Preproc: "bold #6E7781",
            Comment.Special: "bold italic #6E7781",
            Keyword: "bold #0550AE",
            Keyword.Constant: "bold #0550AE",
            Keyword.Declaration: "bold #0550AE",
            Keyword.Namespace: "bold #0550AE",
            Keyword.Pseudo: "bold #0550AE",
            Keyword.Reserved: "bold #0550AE",
            Keyword.Type: "bold #953800",
            Name: "#000000",
            Name.Attribute: "bold #116329",
            Name.Builtin: "bold #0550AE",
            Name.Builtin.Pseudo: "bold #0550AE",
            Name.Class: "bold #953800",
            Name.Constant: "bold #0550AE",
            Name.Decorator: "bold #8250DF",
            Name.Entity: "bold #8250DF",
            Name.Exception: "bold #CF222E",
            Name.Function: "bold #8250DF",
            Name.Property: "bold #0550AE",
            Name.Label: "bold #0550AE",
            Name.Namespace: "bold #0550AE",
            Name.Other: "#000000",
            Name.Tag: "bold #116329",
            Name.Variable: "#000000",
            Name.Variable.Class: "#000000",
            Name.Variable.Global: "#000000",
            Name.Variable.Instance: "#000000",
            Number: "#0550AE",
            Operator: "#0550AE",
            Operator.Word: "bold #0550AE",
            Punctuation: "#000000",
            String: "#116329",
            String.Doc: "italic #6E7781",
            String.Escape: "bold #0550AE",
            String.Regex: "bold #116329",
            String.Symbol: "bold #0550AE",
            Generic.Heading: "bold #0550AE",
            Generic.Subheading: "bold #0550AE",
            Generic.Deleted: "#CF222E",
            Generic.Inserted: "#116329",
            Generic.Error: "#CF222E",
            Generic.Emph: "italic",
            Generic.Strong: "bold",
            Generic.Prompt: "bold #0550AE",
            Generic.Output: "#000000",
            Generic.Traceback: "#CF222E",
            Error: "bold #CF222E",
        }
except ImportError:
    ZzmLightCodeStyle = "tango"


class ZzmCodeBlockElement(CodeBlock):
    """Custom code block renderer with card panels and high-contrast theme."""

    def __rich_console__(self, console: Any, options: Any) -> Any:
        """渲染不依赖固定白色前景的代码卡片，并把边框样式限制在边框本身。

        深色方案使用 Rich 的 ANSI 自适应主题，使纯文本命令继承终端默认前景；
        即使某些 Windows Terminal 无法正确报告背景，白底也不会再收到 Monokai
        的固定近白色文本。浅色探测成功时仍使用明确的深色高对比语法主题。
        """
        from rich import box
        from rich.panel import Panel
        from rich.syntax import Syntax

        is_light = _is_light_terminal_background()
        code = str(self.text).rstrip()
        lexer_name = self.lexer_name or "text"
        syntax_theme = ZzmLightCodeStyle if is_light else "ansi_dark"
        border_style = "dim #D0D7DE" if is_light else "dim #3B4252"
        syntax = Syntax(
            code,
            lexer_name,
            theme=syntax_theme,
            word_wrap=True,
            padding=(0, 1),
            background_color="default",
        )
        title = f"[dim]{lexer_name}[/dim]" if lexer_name and lexer_name != "text" else None
        yield Panel(
            syntax,
            title=title,
            title_align="right",
            box=box.ROUNDED,
            border_style=border_style,
            padding=(0, 0),
        )


class ZzmHeadingElement(Heading):
    """Custom heading renderer with left alignment and panel containers for H1."""

    def __rich_console__(self, console: Any, options: Any) -> Any:
        from rich import box
        from rich.panel import Panel
        from rich.text import Text

        is_light = _is_light_terminal_background()
        border_style = "dim #D0D7DE" if is_light else "dim #3B4252"
        text = self.text
        text.justify = "left"
        if self.tag == "h1":
            yield Panel(
                text,
                box=box.ROUNDED,
                style=border_style,
            )
        else:
            yield Text("")
            yield text


try:
    from rich.markdown import ListItem
except ImportError:
    ListItem = object


class ZzmListItemElement(ListItem):
    """Safe list item that never crashes on non-UTF-8 console encoding."""

    def render_bullet(self, console: Any, options: Any) -> Any:
        try:
            yield from super().render_bullet(console, options)
        except UnicodeEncodeError:
            from rich.segment import Segment

            indent = " " * (self.level * 2)
            yield Segment(f"{indent}* ")
            yield from console.render(self.elements, options)


class ZzmMarkdown(Markdown):
    """GFM-enabled Markdown renderer with custom elements and zero monkeypatching."""

    elements = {
        **getattr(Markdown, "elements", {}),
        "code_block": ZzmCodeBlockElement,
        "fence": ZzmCodeBlockElement,
        "heading_open": ZzmHeadingElement,
        "list_item_open": ZzmListItemElement,
        "table_open": TableElement,
        "thead_open": TableSectionElement,
        "tbody_open": TableSectionElement,
        "tr_open": TableRowElement,
        "th_open": TableCellElement,
        "td_open": TableCellElement,
    }

    def __init__(self, markup: str, **kwargs: Any) -> None:
        super().__init__(markup, **kwargs)
        try:
            from markdown_it import MarkdownIt

            self.parsed = (
                MarkdownIt("gfm-like")
                .enable("table")
                .enable("strikethrough")
                .parse(markup)
            )
        except ImportError:
            pass


def _install_markdown_code_style_patch() -> None:
    """Backward compatibility hook for test runners and plugins."""
    pass


def _compact_reasoning_for_display(text: str) -> str:
    """Keep visible reasoning as a short progress hint, not a transcript wall."""
    compact = " ".join(text.split())
    if len(compact) <= MAX_VISIBLE_REASONING_CHARS:
        return compact
    return compact[: MAX_VISIBLE_REASONING_CHARS - 3].rstrip() + "..."

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

    def get_menu_item_fragments(
        completion: Any,
        is_current_completion: bool,
        width: int,
        space_after: bool = False,
    ) -> Any:
        if not is_current_completion:
            return original_item_fragments(completion, is_current_completion, width, space_after)

        text, text_width = menus._trim_formatted_text(completion.display, width)
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

    menus._get_menu_item_fragments = get_menu_item_fragments
    menus.CompletionsMenuControl._get_menu_item_meta_fragments = get_menu_item_meta_fragments
    menus._zzm_agent_completion_patch = True


def _is_light_terminal_background() -> bool:
    """按显式覆盖、终端自身信息、最后才是系统主题判断浅色背景。

    Windows 的应用主题不等于终端 Profile：用户可能使用深色系统界面和白色终端。
    因此 ``COLORFGBG`` 与控制台缓冲区颜色必须优先于注册表主题，否则会错误选择
    Monokai 的浅色文字并在白底上失去对比度。无法探测时仍保守回退系统设置。
    """
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

    if os.name == "nt":
        try:
            import winreg

            key = winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize",
            )
            val, _ = winreg.QueryValueEx(key, "AppsUseLightTheme")
            if val == 1:
                return True
            if val == 0:
                return False
        except Exception:
            pass

    return False


def get_theme_palette() -> dict[str, str]:
    """Return an adaptive high-contrast color palette based on terminal background."""
    is_light = _is_light_terminal_background()
    if is_light:
        return {
            "primary": "#0969DA",       # Deep Royal Blue
            "secondary": "#0550AE",     # Deep Indigo
            "accent": "#8250DF",        # Deep Purple
            "warning": "#9A6700",       # Deep Warm Amber
            "success": "#1A7F37",       # Deep Forest Green
            "danger": "#CF222E",        # Deep Red
            "text": "default",          # Standard terminal text (Pure Black in light mode)
            "text_dim": "#57606A",      # High-contrast Slate Grey
            "border": "#D0D7DE",        # Crisp light border
            "syntax_theme": "tango",    # Tango pure-contrast code theme
        }
    else:
        return {
            "primary": "#61AFEF",
            "secondary": "#56B6C2",
            "accent": "#C678DD",
            "warning": "#E5C07B",
            "success": "#98C379",
            "danger": "#E06C75",
            "text": "default",
            "text_dim": "#ABB2BF",
            "border": "#3B4252",
            "syntax_theme": "monokai",
        }


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
    """为斜杠命令、``$Skill`` 和 ``@mcp:`` 提供前缀模糊补全菜单。

    斜杠只匹配命令；美元前缀只读取已经发现的本地 Skill，不混入 MCP 工具。
    MCP 使用独立前缀读取延迟工具轻量目录，选择候选只插入标识，不执行工具。
    Skill 候选来自运行时管理器的轻量目录，选择后替换当前 ``$`` 词元，正文仍由
    正常任务执行链路按需加载。缺少 Skill 管理器时保持原有斜杠补全行为。
    """

    def __init__(
        self,
        commands_meta: dict[str, str],
        skill_manager: Any | None = None,
        tool_exposure_manager: Any | None = None,
    ) -> None:
        """保存命令、Skill 与 MCP 轻量目录；构造阶段不读取正文或执行工具。"""
        self._commands_meta = commands_meta
        self._skill_manager = skill_manager
        self._tool_exposure_manager = tool_exposure_manager
        if skill_manager is not None and not getattr(skill_manager, "catalog", None):
            skill_manager.discover()

    def get_completions(self, document: Any, complete_event: Any) -> Iterable[Any]:
        """根据活动前缀返回命令、Skill 或 MCP 候选，不修改输入缓冲区。"""
        text_before_cursor = document.text_before_cursor
        skill_token = _skill_token_before_cursor(text_before_cursor)
        mcp_token = _mcp_token_before_cursor(text_before_cursor)
        if (
            not text_before_cursor.startswith("/")
            and skill_token is None
            and mcp_token is None
        ):
            return

        try:
            from prompt_toolkit.completion import Completion
        except ImportError as exc:
            raise RuntimeError("prompt_toolkit is required for slash completion.") from exc

        if mcp_token is not None:
            if self._tool_exposure_manager is None:
                return
            query = mcp_token[len("@mcp:"):]
            for candidate in self._tool_exposure_manager.completion_candidates(query):
                value = candidate["insert_text"]
                yield Completion(
                    text=value,
                    start_position=-len(mcp_token),
                    display=value,
                    display_meta=(
                        f"MCP · {candidate['server']} · {candidate['description']}"
                    ),
                )
            return

        if text_before_cursor.startswith("/"):
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
            return

        if self._skill_manager is None or skill_token is None:
            return
        query = skill_token[1:].casefold()
        disabled = getattr(self._skill_manager, "disabled", set())
        definitions = sorted(
            getattr(self._skill_manager, "catalog", {}).values(),
            key=lambda item: item.name.casefold(),
        )
        for definition in definitions:
            if not definition.enabled or definition.name.casefold() in disabled:
                continue
            if not _skill_name_matches(query, definition.name):
                continue
            value = f"${definition.name}"
            yield Completion(
                text=value,
                start_position=-len(skill_token),
                display=value,
                display_meta=f"Skill · {definition.description}",
            )


def _slash_command_matches(query: str, command: str) -> bool:
    if query == "/":
        return True

    compact_query = query.lstrip("/").replace(" ", "")
    compact_command = command.lower().lstrip("/").replace(" ", "")
    return _is_prefix_subsequence(compact_query, compact_command)


def _skill_token_before_cursor(text_before_cursor: str) -> str | None:
    """返回光标前最后一个 ``$名称`` 词元；环境变量和普通金额不会跨空白匹配。"""
    match = re.search(r"(?:^|\s)(\$[A-Za-z0-9_-]*)$", text_before_cursor)
    return match.group(1) if match is not None else None


def _mcp_token_before_cursor(text_before_cursor: str) -> str | None:
    """返回光标前完整 ``@mcp:查询`` 词元，其他 @ 提及不会触发工具菜单。"""
    match = re.search(r"(?:^|\s)(@mcp:[A-Za-z0-9_.-]*)$", text_before_cursor, re.IGNORECASE)
    return match.group(1) if match is not None else None


def _skill_name_matches(query: str, name: str) -> bool:
    """复用命令的首字符约束子序列匹配，空查询会展示全部可用 Skill。"""
    compact_query = query.replace(" ", "")
    compact_name = name.casefold().replace(" ", "")
    return _is_prefix_subsequence(compact_query, compact_name)


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
    p = get_theme_palette()
    is_light = _is_light_terminal_background()
    if is_light:
        theme = Theme(
            {
                "markdown.h1": "bold #0969DA",
                "markdown.h2": "bold #8250DF",
                "markdown.h3": "bold #9A6700",
                "markdown.h4": "bold #1A7F37",
                "markdown.strong": "bold #1A7F37",
                "markdown.em": "italic",
                "markdown.code": "bold #0550AE",
                "markdown.code_block": "dim",
                "markdown.block_quote": "dim italic",
                "markdown.hr": "dim #D0D7DE",
                "markdown.link": "underline #0969DA",
                "markdown.link_url": "dim underline",
            }
        )
    else:
        theme = Theme(
            {
                "markdown.h1": f"bold {p['primary']}",
                "markdown.h2": f"bold {p['secondary']}",
                "markdown.h3": f"bold {p['warning']}",
                "markdown.h4": f"bold {p['accent']}",
                "markdown.strong": f"bold {p['success']}",
                "markdown.em": "italic",
                "markdown.code": f"bold {p['secondary']}",
                "markdown.code_block": "none",
                "markdown.block_quote": f"dim italic {p['text_dim']}",
                "markdown.hr": f"dim {p['border']}",
                "markdown.link": f"underline {p['primary']}",
                "markdown.link_url": "dim underline",
            }
        )
    return Console(
        highlight=False,
        theme=theme,
    )


try:
    from prompt_toolkit.lexers import Lexer
    from prompt_toolkit.document import Document
except ImportError:
    Lexer = object
    Document = None


class SlashCommandLexer(Lexer):
    """Lexer that highlights slash commands in real-time as the user types them."""
    def lex_document(self, document: Any) -> Any:
        def get_line_tokens(line_number: int) -> list[tuple[str, str]]:
            line = document.lines[line_number]
            if line.startswith("/"):
                parts = line.split(" ", 1)
                tokens = [("class:prompt-command", parts[0])]
                if len(parts) > 1:
                    tokens.append(("", " " + parts[1]))
                return tokens
            return [("", line)]
        return get_line_tokens


def render_notification(console: Any, message: str, level: str = "system") -> None:
    """Render a unified styled notification box instead of raw print messages."""
    try:
        from rich.text import Text
        from rich.panel import Panel
    except ImportError:
        console.print(f"[{level.upper()}] {message}")
        return

    p = get_theme_palette()
    icon_mapping = {
        "success": ("✔ SUCCESS", p["success"]),
        "warning": ("⚠ WARNING", p["warning"]),
        "error": ("✘ ERROR", p["danger"]),
        "system": ("⚡ SYSTEM", p["secondary"]),
    }
    
    icon_text, color = icon_mapping.get(level, ("⚡ SYSTEM", p["secondary"]))
    
    styled_message = Text()
    styled_message.append(f"{icon_text} ", style=f"bold {color}")
    styled_message.append("│ ", style=f"dim {p['border']}")
    styled_message.append(message, style="default")
    
    panel = Panel(
        styled_message,
        border_style=p["border"],
        padding=(0, 1),
        expand=False,
    )
    console.print(panel)


def render_error_card(console: Any, exc: Exception, runtime: dict[str, Any] | None = None) -> None:
    """Render a beautiful, actionable error guide card instead of a raw traceback."""
    try:
        from rich.panel import Panel
        from rich.text import Text
        from rich import box
    except ImportError:
        console.print(f"[red]Error: {exc}[/red]")
        return

    p = get_theme_palette()
    from zzm_agent.cli_support.repl import format_runtime_exception
    clean_msg = format_runtime_exception(exc, runtime)
    lower_msg = clean_msg.lower()

    title = f"[bold {p['danger']}]✘ 执行遭遇错误 (Execution Error)[/]"
    diagnosis = "系统在执行当前指令或请求大语言模型时发生异常。"
    steps = []

    if "404" in lower_msg or "not found" in lower_msg:
        diagnosis = "API 接口地址 (Base URL) 或模型名称可能配置错误 (HTTP 404)。"
        steps = [
            f"1. 检查 `config.yaml` 中的 [bold {p['primary']}]model.base_url[/] 是否正确。",
            "2. 检查使用的模型名是否在服务提供商支持的列表里（可使用 `/models` 确认）。",
            "3. 如果使用的是本地模型，请确保如 Ollama 或 LocalAI 服务已经正常启动。"
        ]
    elif "401" in lower_msg or "unauthorized" in lower_msg or "api key" in lower_msg or "api_key" in lower_msg:
        diagnosis = "鉴权失败，大语言模型 API Key 无效或未配置 (HTTP 401)。"
        steps = [
            f"1. 确认已在根目录的 [bold {p['primary']}].env[/] 文件中配置了正确的 API Key。",
            "2. 检查环境变量或配置中的密钥是否过期或被撤销。"
        ]
    elif "connection" in lower_msg or "connect" in lower_msg or "timeout" in lower_msg or "dns" in lower_msg:
        diagnosis = "网络连接故障：无法连接到大语言模型服务提供商 (Timeout/Connection Refused)。"
        steps = [
            "1. 请检查您的网络连接，以及代理/VPN 规则是否允许访问该 API 域名。",
            "2. 尝试在终端中执行 ping 或 curl 测试以验证网络通畅度。"
        ]
    elif "quota" in lower_msg or "billing" in lower_msg or "insufficient" in lower_msg:
        diagnosis = "余额不足或超出了 API 服务商的使用额度限制 (HTTP 429)。"
        steps = [
            "1. 请检查您在服务提供商处的账户余额或账单状态。",
            "2. 稍等片刻（通常为 1 分钟左右）再次重试，避免触发频繁请求限制。"
        ]
    else:
        steps = [
            "1. 详细阅读下方给出的具体错误详细信息。",
            f"2. 若属于本地代码运行异常，可以使用 [bold {p['primary']}]--debug[/] 参数启动以打印完整堆栈。",
            "3. 如有必要，请尝试使用 `/new` 开启一个干净的会话或者使用 `/reload` 重载插件。"
        ]

    content = Text()
    content.append("故障分析: ", style=f"bold {p['warning']}")
    content.append(diagnosis + "\n\n", style="default")
    content.append("原始错误说明: ", style=f"bold {p['danger']}")
    content.append(clean_msg + "\n\n", style=f"dim {p['text_dim']}")
    
    content.append("推荐排查与修复步骤:\n", style=f"bold {p['success']}")
    for step in steps:
        content.append(f"  {step}\n")
    content.rstrip()

    console.print(
        Panel(
            content,
            title=title,
            title_align="left",
            border_style=p["danger"],
            box=box.ROUNDED,
            padding=(1, 2),
            expand=False,
        )
    )


def build_bottom_toolbar(runtime: dict[str, Any] | None = None):
    """Build the shared prompt-toolkit bottom toolbar using a Powerline-like layout."""
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
    context_window = getattr(loop, "last_context_window", {}) or {}
    context_limit = int(
        context_window.get("max_context_tokens", 0)
        or getattr(store, "max_context_tokens", 0)
        or 0
    )
    last_usage = getattr(loop, "last_turn_usage", None)
    context_used = getattr(last_usage, "prompt_tokens", 0) or 0

    # Format using Powerline-style badge blocks
    return HTML(
        f'<span class="workspace"> 📂 {workspace_path} </span>'
        f'<span class="model"> 🤖 Model: {model} </span>'
        f'<span class="context"> 🧠 Context: {context_used}/{context_limit} </span>'
    )


def build_prompt_session(workspace: str | Path, runtime: dict[str, Any] | None = None):
    """Create an optional prompt_toolkit input session with history and style custom overrides."""
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
    palette = get_theme_palette()
    style = Style.from_dict({
        "prompt": "ansicyan bold",
        "bottom-toolbar": "noreverse bg:default fg:default",
        "bottom-toolbar.workspace": "noreverse ansigreen bold",
        "bottom-toolbar.model": "noreverse ansiblue bold",
        "bottom-toolbar.context": "noreverse ansimagenta bold",
        "bottom-toolbar.session": "noreverse ansiyellow bold",
        "bottom-toolbar.text": "noreverse",
        "prompt-command": f"{palette['secondary']} bold",
        "completion-menu": "bg:default fg:default",
        "completion-menu.completion": "noreverse bg:default #666666",
        "completion-menu.completion.current": f"noreverse bg:default {palette['primary']} bold",
        "completion-menu.meta.completion": "noreverse bg:default #8a8a8a",
        "completion-menu.meta.completion.current": f"noreverse bg:default {palette['primary']} bold",
        "scrollbar.background": "bg:default",
        "scrollbar.button": "bg:default",
        "scrollbar.arrow": "bg:default",
    })
    
    completer = None
    bottom_toolbar = None
    kb = None
    
    if runtime:
        commands_meta = {
            "/help": "显示帮助信息",
            "/tools": "列出所有注册的工具",
            "/reload": "重新加载本地工具插件",
            "/models": "列出当前 base URL 可用模型",
            "/model": "查看或切换当前模型",
            "/config": "显示当前生效配置和来源",
            "/status": "显示当前会话、模型、Token 和运行状态",
            "/stream": "查看或切换流式输出",
            "/memory": "显示最近历史和压缩状态",
            "/instructions": "显示加载的项目指令文件",
            "/sessions": "列出所有已知的会话",
            "/session": "切换到指定的历史会话",
            "/resume": "恢复最近或指定历史会话",
            "/new": "开启一轮全新的对话",
            "/permissions": "显示当前权限账本",
            "/artifacts": "列出或预览 Artifact",
            "/plan": "显示当前计划或本地计划文件",
            "/review": "对当前 git diff 做只读审查",
            "/git": "查看状态或执行可回滚的 stage/unstage",
            "/commit-message": "根据 diff 和测试证据生成提交说明",
            "/branch": "根据当前改动建议分支名",
            "/pr": "生成包含测试与风险的 PR 描述",
            "/ci": "分析 CI 日志并关联 Artifact",
            "/undo": "查看可撤销变更状态",
            "/skills": "显示 Skills 集成状态",
            "/mcp": "显示 MCP 集成状态",
            "/remember": "添加一条长期的语义记忆",
            "/memory-disable": "禁用匹配关键字的长期记忆",
            "/memory-enable": "重新启用匹配关键字的长期记忆",
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
        completer = SlashCommandCompleter(
            commands_meta,
            runtime.get("skills"),
            runtime.get("tool_exposure"),
        )
        
        def get_bottom_toolbar():
            return build_bottom_toolbar(runtime)
            
        bottom_toolbar = get_bottom_toolbar

        try:
            from prompt_toolkit.key_binding import KeyBindings
            kb = KeyBindings()
            
            @kb.add("/")
            def _(event: Any) -> None:
                event.current_buffer.insert_text("/")
                event.current_buffer.start_completion(select_first=False)

            @kb.add("$")
            def _(event: Any) -> None:
                event.current_buffer.insert_text("$")
                event.current_buffer.start_completion(select_first=False)

            @kb.add("@")
            def _(event: Any) -> None:
                """插入 @ 并启动补全；只有继续输入 ``mcp:`` 后才展示候选。"""
                event.current_buffer.insert_text("@")
                event.current_buffer.start_completion(select_first=False)
        except ImportError:
            kb = None
 
    try:
        prompt_session = PromptSession(
            history=FileHistory(str(history_path)),
            style=style,
            completer=completer,
            lexer=SlashCommandLexer() if completer else None,
            bottom_toolbar=bottom_toolbar,
            key_bindings=kb,
            complete_while_typing=True,
            reserve_space_for_menu=6,
        )
        return prompt_session
    except Exception:
        return None


def read_repl_input(console: Any, prompt_session: Any | None) -> str:
    """Read one user input line, using prompt_toolkit when available, with success/failure status colors."""
    if console.__class__.__name__ != "Console":
        if prompt_session is None:
            return console.input("[bold #61AFEF]>[/bold #61AFEF] ").strip()
        try:
            from prompt_toolkit.formatted_text import HTML
            prompt_text = HTML('<style fg="ansicyan">></style> ')
        except ImportError:
            prompt_text = [("class:prompt", "> ")]
        return prompt_session.prompt(prompt_text).strip()

    last_success = getattr(console, "_zzm_last_turn_success", True)
    prompt_color = "ansigreen" if last_success else "ansired"
    
    if prompt_session is None:
        color_markup = "bold green" if last_success else "bold red"
        return console.input(f"[{color_markup}]zzm-agent ❯[/{color_markup}] ").strip()
        
    try:
        from prompt_toolkit.formatted_text import HTML
        prompt_text = HTML(f'<style fg="{prompt_color}">zzm-agent ❯</style> ')
    except ImportError:
        prompt_text = [("class:prompt", "zzm-agent ❯ ")]
        
    return prompt_session.prompt(prompt_text).strip()


def render_reply(console: Any, reply: str) -> None:
    """
    Render an assistant reply using Rich Markdown when available.

    Args:
        console: Console-like object used for output.
        reply: Final assistant reply text to render.
    """
    try:
        md = ZzmMarkdown(reply)
        console.print(md)
    except Exception:
        # Fallback to plain print
        console.print(reply)


def _strip_reply_emoji(reply: str) -> str:
    """Remove emoji glyphs from assistant output for a cleaner terminal display."""
    return _EMOJI_PATTERN.sub("", reply)


def _plain_terminal_reply(reply: str) -> str:
    """Render assistant Markdown as quiet terminal text."""
    text = _strip_reply_emoji(reply)
    cleaned: list[str] = []
    in_fence = False

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
                line = heading_match.group(1)
            line = re.sub(r"\*\*(.*?)\*\*", r"\1", line)
            line = re.sub(r"__(.*?)__", r"\1", line)
            line = re.sub(r"`([^`]+)`", r"\1", line)
            line = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", r"\1 (\2)", line)

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
        # Count ``` fences that appear at line starts
        fences = re.findall(r"(?m)^\s*```", text)
        if len(fences) % 2 != 0:
            # We are inside an unclosed code block, do not split inside the block
            last_fence_idx = text.rfind("```")
            boundary_before_fence = text[:last_fence_idx].rfind("\n\n")
            if boundary_before_fence == -1:
                return "", text
            split_at = boundary_before_fence + 2
            return text[:split_at], text[split_at:]

        boundary = text.rfind("\n\n")
        if boundary == -1:
            return "", text

        prefix = text[:boundary]
        lines = [line.strip() for line in prefix.splitlines() if line.strip()]
        if lines and lines[-1].startswith("|"):
            return "", text

        split_at = boundary + 2
        return text[:split_at], text[split_at:]


class PlainTextRenderer:
    """Render stream events as plain text for non-Rich or redirected output."""

    def __init__(self, console: Any):
        """初始化纯文本流 Renderer，并准备工具参数与专用 Renderer 状态。

        console 接收最终输出；实例会缓存分段到达的工具参数，直到结果事件到达。
        所有缓存仅服务当前渲染会话，不修改 Agent 状态；未知工具自动走纯文本降级。
        """
        self.console = console
        self._content = ""
        self._reasoning = ""
        self._final_rendered = False
        self._separator_printed = False
        self._seen_process = False
        self._printed_tool_calls: set[str] = set()
        self._tool_arguments: dict[str, str] = {}
        self._tool_names: dict[str, str] = {}
        self._tool_renderers = build_local_tool_renderer_registry()

    def render_event(self, event: ModelStreamEvent) -> None:
        if event.kind is ModelStreamEventKind.STATUS:
            if event.text and event.text != "turn.started":
                self.console.print(f"Status: {event.text}")
            return
        if event.kind is ModelStreamEventKind.REASONING_SUMMARY:
            self._append_reasoning(event.text)
            return
        if event.kind is ModelStreamEventKind.TOOL_CALL_DELTA:
            self._render_tool_call_delta(event)
            return
        if event.kind is ModelStreamEventKind.TOOL_RESULT:
            self._render_tool_result(event, rich=False)
            return
        if event.kind is ModelStreamEventKind.ERROR:
            self.console.print(f"Error: {event.text}")
            return
        if event.kind is ModelStreamEventKind.TERMINATION:
            self._render_termination(event)
            return
        if event.kind is ModelStreamEventKind.CONTENT_DELTA:
            self._flush_reasoning()
            if self._seen_process:
                self._print_separator()
            self._content += event.text or ""
            return
        if event.kind is ModelStreamEventKind.FINAL_MESSAGE:
            if event.text:
                self.render_final(event.text)

    def should_stop_working_status(self, event: ModelStreamEvent) -> bool:
        """Return whether this event needs a clean line outside the live status."""
        return event.kind is not ModelStreamEventKind.STATUS

    def render_final(self, text: str) -> None:
        if self._final_rendered:
            return
        self._flush_reasoning()
        self._print_separator()
        self.console.print(text)
        self._content = ""
        self._final_rendered = True

    def finish(self, fallback_text: str = "") -> None:
        if self._final_rendered:
            return
        text = self._content or fallback_text
        if text:
            self.render_final(text)
        elif self._seen_process:
            self.render_final("Tool execution completed, but the model returned no final summary.")

    def _print_separator(self) -> None:
        if self._separator_printed:
            return
        self.console.print("---")
        self._separator_printed = True

    def _append_reasoning(self, text: str) -> None:
        if not text:
            return
        self._seen_process = True
        self._reasoning += text

    def _flush_reasoning(self) -> None:
        text = _compact_reasoning_for_display(self._reasoning)
        if not text:
            return
        self.console.print(f"Reasoning: {text}")
        self._reasoning = ""

    def _render_tool_call_delta(self, event: ModelStreamEvent) -> None:
        """收集流式工具名与参数，并在首次可识别时输出动态活动描述。

        参数可能跨多个事件到达，因此按 tool_call_id 累加。首次事件缺少 ID 时
        使用工具名作为兼容键；重复片段不会重复打印 Running 行。
        """
        key = event.tool_call_id or event.tool_name or "<pending>"
        if event.arguments_delta:
            self._tool_arguments[key] = self._tool_arguments.get(key, "") + event.arguments_delta
        if event.tool_name:
            self._tool_names[key] = event.tool_name
        if not event.tool_name:
            return
        self._flush_reasoning()
        if key in self._printed_tool_calls:
            return
        self._seen_process = True
        self._printed_tool_calls.add(key)
        context = self._tool_context(event)
        view = self._tool_renderers.select(context).render_use(context)
        self.console.print(f"Running {view.text}")

    def _tool_context(self, event: ModelStreamEvent) -> ToolRenderContext:
        """从流事件和已缓存参数建立只读 ToolRenderContext。

        结果事件优先使用 AgentLoop 提供的完整 arguments；流式调用阶段则尝试
        解析累计 JSON。解析失败只影响描述精度，不阻断渲染或工具执行。
        """
        key = event.tool_call_id or event.tool_name or "<pending>"
        arguments = event.metadata.get("arguments")
        if not isinstance(arguments, dict):
            arguments = parse_tool_arguments(self._tool_arguments.get(key, ""))
        return ToolRenderContext(
            tool_name=event.tool_name or self._tool_names.get(key, "tool"),
            tool_call_id=event.tool_call_id or key,
            arguments_summary=dict(arguments),
            risk_level=str(event.metadata.get("risk_level") or "unknown"),
        )

    def _render_tool_result(self, event: ModelStreamEvent, *, rich: bool) -> None:
        """用 ToolResult 和专用 Renderer 展示完成或失败，不解析自然语言状态。

        新事件携带完整 tool_result 记录；旧调用只带 text 时会构造兼容结果。
        输出始终保留 Ran/Failed 状态词，Rich 模式仅增加样式，不改变文本事实。
        """
        self._flush_reasoning()
        self._seen_process = True
        context = self._tool_context(event)
        record = event.metadata.get("tool_result")
        if isinstance(record, dict):
            result = ToolResult.from_record(record)
        else:
            result = ToolResult.from_text(
                tool_call_id=context.tool_call_id,
                tool_name=context.tool_name,
                status=str(event.metadata.get("status") or "success"),
                content=event.text or "",
            )
        renderer = self._tool_renderers.select(context)
        failed = result.status not in {"success", "completed", "ok"}
        view = renderer.render_error(context, result) if failed else renderer.render_result(context, result)
        label = "Failed" if failed else "Ran"
        if rich:
            style = "red" if failed else "bold"
            detail = f" [dim]{view.text}[/dim]" if view.text else ""
            self.console.print(f"[{style}]{label}[/{style}] [cyan]{context.tool_name}[/cyan]{detail}")
        else:
            detail = f": {view.text}" if view.text else ""
            self.console.print(f"{label} {context.tool_name}{detail}")

    def _render_termination(self, event: ModelStreamEvent) -> None:
        status = str(event.metadata.get("status") or event.text or "unknown")
        reason = str(event.metadata.get("reason") or "unknown")
        provider_reason = event.metadata.get("provider_finish_reason")
        provider_text = (
            f", provider={provider_reason}" if provider_reason else ""
        )
        self.console.print(f"Ended: {status} ({reason}{provider_text})")


class TerminalRenderer(PlainTextRenderer):
    """Render normalized model stream events for the interactive terminal."""

    def __init__(self, console: Any):
        super().__init__(console)
        self._markdown = MarkdownStreamRenderer(console)
        self._content_seen = False

    def render_event(self, event: ModelStreamEvent) -> None:
        if event.kind is ModelStreamEventKind.STATUS:
            return
        if event.kind is ModelStreamEventKind.REASONING_SUMMARY:
            self._append_reasoning(event.text)
            return
        if event.kind is ModelStreamEventKind.TOOL_CALL_DELTA:
            self._render_tool_call_delta(event)
            return
        if event.kind is ModelStreamEventKind.TOOL_RESULT:
            self._render_tool_result(event, rich=True)
            return
        if event.kind is ModelStreamEventKind.ERROR:
            self.console.print(f"[red]Error:[/red] {event.text}")
            return
        if event.kind is ModelStreamEventKind.TERMINATION:
            self._markdown.flush()
            status = str(event.metadata.get("status") or event.text or "unknown")
            reason = str(event.metadata.get("reason") or "unknown")
            provider_reason = event.metadata.get("provider_finish_reason")
            provider_text = (
                f"; provider={provider_reason}" if provider_reason else ""
            )
            style = "green" if status == "completed" else "yellow"
            self.console.print(
                f"[{style}]Ended: {status}[/{style}] [dim]({reason}{provider_text})[/dim]"
            )
            return
        if event.kind is ModelStreamEventKind.CONTENT_DELTA:
            if event.text:
                self._flush_reasoning()
                if self._seen_process:
                    self._print_separator()
                self._content_seen = True
                self._content += event.text
                self._markdown.push(event.text)
            return
        if event.kind is ModelStreamEventKind.FINAL_MESSAGE:
            self._markdown.flush()
            if event.text:
                self.render_final(event.text)

    def render_final(self, text: str) -> None:
        if self._final_rendered:
            return
        self._flush_reasoning()
        if self._content_seen:
            self._print_separator()
        elif text:
            self._print_separator()
            render_reply(self.console, text)
        self._content = ""
        self._final_rendered = True

    def finish(self, fallback_text: str = "") -> None:
        self._markdown.flush()
        super().finish(fallback_text)

    def _print_separator(self) -> None:
        if self._separator_printed:
            return
        try:
            from rich.rule import Rule
            is_light = _is_light_terminal_background()
            border_style = "dim #D0D7DE" if is_light else "dim #3B4252"
            self.console.print(Rule(style=border_style))
        except Exception:
            self.console.print("---")
        self._separator_printed = True

    def _flush_reasoning(self) -> None:
        text = _compact_reasoning_for_display(self._reasoning)
        if not text:
            return
        self.console.print(f"[black]Reasoning:[/black] [dim]{text}[/dim]")
        self._reasoning = ""

    def _render_tool_call_delta(self, event: ModelStreamEvent) -> None:
        """复用纯文本参数聚合，并把首次活动描述输出为 Rich 样式。"""
        key = event.tool_call_id or event.tool_name or "<pending>"
        if event.arguments_delta:
            self._tool_arguments[key] = self._tool_arguments.get(key, "") + event.arguments_delta
        if event.tool_name:
            self._tool_names[key] = event.tool_name
        if not event.tool_name:
            return
        self._flush_reasoning()
        if key in self._printed_tool_calls:
            return
        self._seen_process = True
        self._printed_tool_calls.add(key)
        context = self._tool_context(event)
        view = self._tool_renderers.select(context).render_use(context)
        self.console.print(f"[bold]Running[/bold] [cyan]{view.text}[/cyan]")


def build_terminal_renderer(console: Any) -> PlainTextRenderer:
    """Select a Rich terminal renderer or plain text fallback."""
    if console.__class__.__name__ != "Console":
        return PlainTextRenderer(console)
    if not getattr(console, "is_terminal", True):
        return PlainTextRenderer(console)
    return TerminalRenderer(console)


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
    Render a structured help message with categorized commands.
    """
    if console.__class__.__name__ != "Console":
        help_text = """
Available Commands:
/help         - Show this help message
/tools        - List all registered tools
/reload       - Reload plugin tools from disk
/models       - List models from the configured base URL
/model <id>   - Show or switch the active model
/status       - Show current session, model, usage, and runtime status
/stream       - Show or change streaming output mode
/memory       - Show recent conversation history and compression state
/instructions - Show loaded AGENTS.md / ZZM.md project instructions
/sessions     - List all known conversation sessions
/session <id> - Switch to a specific session
/resume [id]  - Resume a previous session
/new          - Start a clean conversation session
/permissions  - Show permission ledger summary
/artifacts    - List or preview artifacts
/plan         - Show active or local plan
/review       - Run a read-only review for git diff
/undo         - Show undo availability
/skills       - Show Skills integration status
/mcp          - Show MCP integration status
/remember <f> - Add a long-term semantic memory fact
/memory-disable <k> - Disable long-term memories matching a keyword
/memory-enable <k>  - Re-enable long-term memories matching a keyword
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

    try:
        from rich import box
        from rich.table import Table
        from rich.panel import Panel
        from rich.console import Group
    except ImportError:
        help_text = """
Available Commands:
/help         - Show this help message
/tools        - List all registered tools
/reload       - Reload plugin tools from disk
/models       - List models from the configured base URL
/model <id>   - Show or switch the active model
/status       - Show current session, model, usage, and runtime status
/stream       - Show or change streaming output mode
/memory       - Show recent conversation history and compression state
/instructions - Show loaded AGENTS.md / ZZM.md project instructions
/sessions     - List all known conversation sessions
/session <id> - Switch to a specific session
/resume [id]  - Resume a previous session
/new          - Start a clean conversation session
/permissions  - Show permission ledger summary
/artifacts    - List or preview artifacts
/plan         - Show active or local plan
/review       - Run a read-only review for git diff
/undo         - Show undo availability
/skills       - Show Skills integration status
/mcp          - Show MCP integration status
/remember <f> - Add a long-term semantic memory fact
/memory-disable <k> - Disable long-term memories matching a keyword
/memory-enable <k>  - Re-enable long-term memories matching a keyword
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

    palette = get_theme_palette()

    # Categorize commands into logical tables
    # 1. Session Management
    t_session = Table(show_header=False, box=None, padding=(0, 1))
    t_session.add_column("Command", style=f"bold {palette['secondary']}", width=24)
    t_session.add_column("Desc", style="default")
    t_session.add_row("/new", "开启一轮全新的对话会话")
    t_session.add_row("/sessions", "列出所有已知的历史会话")
    t_session.add_row("/session <id>", "切换到指定的历史会话")
    t_session.add_row("/exit, /quit", "结束并退出当前会话")

    # 2. Model & Output
    t_model = Table(show_header=False, box=None, padding=(0, 1))
    t_model.add_column("Command", style=f"bold {palette['secondary']}", width=24)
    t_model.add_column("Desc", style="default")
    t_model.add_row("/models", "列出当前 base URL 可用模型")
    t_model.add_row("/model [id]", "查看或切换当前模型")
    t_model.add_row("/config", "显示当前生效配置和来源")
    t_model.add_row("/stream [on|off]", "查看或切换流式输出模式")

    # 3. Memory & Facts
    t_memory = Table(show_header=False, box=None, padding=(0, 1))
    t_memory.add_column("Command", style=f"bold {palette['secondary']}", width=24)
    t_memory.add_column("Desc", style="default")
    t_memory.add_row("/memory", "显示最近消息历史与压缩状态")
    t_memory.add_row("/instructions", "显示当前加载的 AGENTS.md / ZZM.md 指令")
    t_memory.add_row("/status", "显示会话、模型、Token 和运行状态")
    t_memory.add_row("/resume [id]", "恢复最近或指定历史会话")
    t_memory.add_row("/permissions", "显示权限账本摘要")
    t_memory.add_row("/artifacts [id]", "列出或预览 Artifact")
    t_memory.add_row("/plan", "显示当前计划或本地计划文件")
    t_memory.add_row("/review", "对 git diff 做只读审查")
    t_memory.add_row("/undo", "查看可撤销变更状态")
    t_memory.add_row("/skills", "显示 Skills 集成状态")
    t_memory.add_row("/mcp", "显示 MCP 集成状态")
    t_memory.add_row("/remember <fact>", "添加一条长期语义记忆")
    t_memory.add_row("/memory-disable <key>", "禁用匹配关键字的长期记忆")
    t_memory.add_row("/memory-enable <key>", "重新启用匹配关键字的长期记忆")
    t_memory.add_row("/forget <key>", "删除匹配关键字的长期记忆")
    t_memory.add_row("/search <key>", "全局搜索历史对话和记忆")
    t_memory.add_row("/semantic", "列出所有长期语义记忆")

    # 4. Prompt Evolution
    t_evolve = Table(show_header=False, box=None, padding=(0, 1))
    t_evolve.add_column("Command", style=f"bold {palette['secondary']}", width=24)
    t_evolve.add_column("Desc", style="default")
    t_evolve.add_row("/evolve run", "基于当前会话优化并生成提示词")
    t_evolve.add_row("/evolve diff", "查看新提示词与旧版本的差异")
    t_evolve.add_row("/evolve apply", "应用刚刚生成的新提示词")
    t_evolve.add_row("/evolve rollback", "回滚到上一个提示词版本")

    # 5. Tools & System
    t_system = Table(show_header=False, box=None, padding=(0, 1))
    t_system.add_column("Command", style=f"bold {palette['secondary']}", width=24)
    t_system.add_column("Desc", style="default")
    t_system.add_row("/tools", "列出所有注册的工具及其描述")
    t_system.add_row("/reload", "重新加载本地工具插件")
    t_system.add_row("/help", "显示本帮助信息")


    help_group = Group(
        f"[bold {palette['primary']}]会话管理 (Session Management)[/]",
        t_session,
        "",
        f"[bold {palette['secondary']}]模型与控制 (Model & Output Control)[/]",
        t_model,
        "",
        f"[bold {palette['warning']}]记忆与事实 (Memory & Facts Knowledge)[/]",
        t_memory,
        "",
        f"[bold {palette['accent']}]提示词优化 (Prompt Evolution)[/]",
        t_evolve,
        "",
        f"[bold {palette['success']}]工具与系统 (Tools & System Debug)[/]",
        t_system,
    )

    console.print(
        Panel(
            help_group,
            title=f"[bold {palette['primary']}]zzm-agent 控制台命令面板[/]",
            title_align="left",
            border_style=palette["border"],
            box=box.ROUNDED,
            padding=(1, 2),
            expand=False,
        )
    )


def render_welcome(console: Any, session_id: str, model: str, workspace: str, tool_count: int) -> None:
    """
    Render a professional welcome panel with a logo and runtime information.
    """
    if console.__class__.__name__ != "Console":
        console.print(f"[bold green]zzm-agent[/bold green] started (Session: {session_id})")
        return

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

    # Modern compact ASCII Logo
    logo_text = (
        "██▀▀▀█▄ ▀██▀▀▀█▄ █▀▄▀█   █▀▀█ █▀▀█ █▀▀▀ █▀▀█ ▀█▀\n"
        "  ▄▄▄█▀   ▄▄▄█▀  █ █ █   █▄▄█ █ ▄█ █▀▀  █  █  █ \n"
        "███████ ███████  █   █   █  █ █▄▄█ █▄▄▄ █  █  █ "
    )
    logo = Text(logo_text, style="bold #56B6C2")
    subtitle = Text("agentic coding console", style="italic dim")
    
    # Info Table with emojis and custom colors
    info_table = Table.grid(padding=(0, 2))
    info_table.add_column(style="dim", justify="right")
    info_table.add_column(style="#DCDCAA")
    
    info_table.add_row("session", f"[#98C379]{session_id}[/]  ")
    info_table.add_row("model", f"[#56B6C2]{model}[/]  ")
    info_table.add_row("workspace", f"[#E5C07B]{workspace}[/]  ")
    info_table.add_row("tools", f"[#C678DD]{tool_count} registered[/]  ")

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
            subtitle="[dim]输入 /help 可以查看支持的命令[/dim]",
            subtitle_align="center",
            expand=False,
            # width=70,
        )
    )
