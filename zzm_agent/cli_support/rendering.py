from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any, Iterable

from zzm_agent.constants import ZZM_AGENT_DIR
from zzm_agent.core.model_stream import ModelStreamEvent, ModelStreamEventKind

_EMOJI_PATTERN = re.compile(
    "["
    "\U0001F1E6-\U0001F1FF"
    "\U0001F300-\U0001FAFF"
    "\U00002700-\U000027BF"
    "\U00002600-\U000026FF"
    "]+"
)
PROMPT_COMPLETION_MENU_RESERVED_LINES = 1

try:
    from rich.markdown import CodeBlock, Heading
except ImportError:
    CodeBlock = None
    Heading = None


def _install_markdown_code_style_patch() -> None:
    if CodeBlock is not None and not getattr(CodeBlock, "_zzm_agent_no_background", False):
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

    if Heading is not None and not getattr(Heading, "_zzm_agent_left_align", False):
        def render_heading(self: Any, console: Any, options: Any) -> Any:
            text = self.text
            text.justify = "left"
            if self.tag == "h1":
                from rich.panel import Panel
                from rich import box
                yield Panel(
                    text,
                    box=box.HEAVY,
                    style="markdown.h1.border",
                )
            else:
                from rich.text import Text
                if self.tag == "h2":
                    yield Text("")
                yield text

        Heading.__rich_console__ = render_heading
        Heading._zzm_agent_left_align = True

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
                "markdown.h1": "bold #61AFEF",
                "markdown.h2": "bold #56B6C2",
                "markdown.h3": "bold #E5C07B",
                "markdown.h4": "bold #C678DD",
                "markdown.strong": "bold #98C379",
                "markdown.em": "italic",
                "markdown.code": "bold #56B6C2",
                "markdown.code_block": "dim",
                "markdown.block_quote": "dim italic",
                "markdown.hr": "dim #3B4252",
                "markdown.link": "underline #61AFEF",
                "markdown.link_url": "dim underline",
            }
        ),
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

    icon_mapping = {
        "success": ("✔ SUCCESS", "#98C379"),
        "warning": ("⚠ WARNING", "#E5C07B"),
        "error": ("✘ ERROR", "#CF222E"),
        "system": ("⚡ SYSTEM", "#56B6C2"),
    }
    
    icon_text, color = icon_mapping.get(level, ("⚡ SYSTEM", "#56B6C2"))
    
    styled_message = Text()
    styled_message.append(f"{icon_text} ", style=f"bold {color}")
    styled_message.append("│ ", style="dim #3B4252")
    styled_message.append(message, style="default")
    
    panel = Panel(
        styled_message,
        border_style="#3B4252",
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

    # Extract clean error message
    from zzm_agent.cli_support.runtime import _format_repl_exception_with_runtime
    clean_msg = _format_repl_exception_with_runtime(exc, runtime)
    lower_msg = clean_msg.lower()

    # Determine diagnosis and solutions
    title = "[bold #CF222E]✘ 执行遭遇错误 (Execution Error)[/]"
    diagnosis = "系统在执行当前指令或请求大语言模型时发生异常。"
    steps = []

    if "404" in lower_msg or "not found" in lower_msg:
        diagnosis = "API 接口地址 (Base URL) 或模型名称可能配置错误 (HTTP 404)。"
        steps = [
            "1. 检查 `config.yaml` 中的 [bold #61AFEF]model.base_url[/] 是否正确。",
            "2. 检查使用的模型名是否在服务提供商支持的列表里（可使用 `/models` 确认）。",
            "3. 如果使用的是本地模型，请确保如 Ollama 或 LocalAI 服务已经正常启动。"
        ]
    elif "401" in lower_msg or "unauthorized" in lower_msg or "api key" in lower_msg or "api_key" in lower_msg:
        diagnosis = "鉴权失败，大语言模型 API Key 无效或未配置 (HTTP 401)。"
        steps = [
            "1. 确认已在根目录的 [bold #61AFEF].env[/] 文件中配置了正确的 API Key。",
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
            "2. 若属于本地代码运行异常，可以使用 [bold #61AFEF]--debug[/] 参数启动以打印完整堆栈。",
            "3. 如有必要，请尝试使用 `/new` 开启一个干净的会话或者使用 `/reload` 重载插件。"
        ]

    content = Text()
    content.append("🔍 故障分析: ", style="bold #E5C07B")
    content.append(f"{diagnosis}\n\n", style="default")
    content.append("📄 原始错误说明: ", style="bold #CF222E")
    content.append(f"{clean_msg}\n\n", style="dim")
    
    content.append("🛠️ 推荐排查与修复步骤:\n", style="bold #98C379")
    for step in steps:
        content.append(f"  {step}\n")
    content.rstrip()

    console.print(
        Panel(
            content,
            title=title,
            title_align="left",
            border_style="#CF222E",
            box=box.ROUNDED,
            padding=(1, 2),
            expand=False
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
    style = Style.from_dict({
        "prompt": "ansicyan bold",
        "bottom-toolbar": "noreverse bg:default fg:default",
        "bottom-toolbar.workspace": "noreverse ansigreen bold",
        "bottom-toolbar.model": "noreverse ansiblue bold",
        "bottom-toolbar.context": "noreverse ansimagenta bold",
        "bottom-toolbar.session": "noreverse ansiyellow bold",
        "bottom-toolbar.text": "noreverse",
        "prompt-command": "ansicyan bold",
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
        completer = SlashCommandCompleter(commands_meta)
        
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
        except ImportError:
            kb = None
 
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
        from rich.markdown import Markdown
        # Render markdown directly using Rich
        md = Markdown(reply)
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
        boundary = text.rfind("\n\n")
        if boundary == -1:
            return "", text
        split_at = boundary + 2
        return text[:split_at], text[split_at:]


class PlainTextRenderer:
    """Render stream events as plain text for non-Rich or redirected output."""

    def __init__(self, console: Any):
        self.console = console
        self._content = ""
        self._reasoning = ""
        self._final_rendered = False
        self._separator_printed = False
        self._seen_process = False
        self._printed_tool_calls: set[str] = set()

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
            self._flush_reasoning()
            self._seen_process = True
            name = event.tool_name or "tool"
            text = f": {event.text}" if event.text else ""
            self.console.print(f"Ran {name}{text}")
            return
        if event.kind is ModelStreamEventKind.ERROR:
            self.console.print(f"Error: {event.text}")
            return
        if event.kind is ModelStreamEventKind.CONTENT_DELTA:
            self._flush_reasoning()
            if self._seen_process:
                self._print_separator()
            self._content += event.text or ""
            return
        if event.kind is ModelStreamEventKind.FINAL_MESSAGE:
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
        text = " ".join(self._reasoning.split())
        if not text:
            return
        self.console.print(f"Reasoning: {text}")
        self._reasoning = ""

    def _render_tool_call_delta(self, event: ModelStreamEvent) -> None:
        if not event.tool_name:
            return
        self._flush_reasoning()
        key = event.tool_call_id or event.tool_name
        if key in self._printed_tool_calls:
            return
        self._seen_process = True
        self._printed_tool_calls.add(key)
        self.console.print(f"Running {event.tool_name}")


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
            self._flush_reasoning()
            self._seen_process = True
            name = event.tool_name or "tool"
            text = f" [dim]{event.text}[/dim]" if event.text else ""
            self.console.print(f"[bold]Ran[/bold] [cyan]{name}[/cyan]{text}")
            return
        if event.kind is ModelStreamEventKind.ERROR:
            self.console.print(f"[red]Error:[/red] {event.text}")
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
            self.console.print(Rule(style="dim #3B4252"))
        except Exception:
            self.console.print("---")
        self._separator_printed = True

    def _flush_reasoning(self) -> None:
        text = " ".join(self._reasoning.split())
        if not text:
            return
        self.console.print(f"[black]Reasoning:[/black] [dim]{text}[/dim]")
        self._reasoning = ""

    def _render_tool_call_delta(self, event: ModelStreamEvent) -> None:
        if not event.tool_name:
            return
        self._flush_reasoning()
        key = event.tool_call_id or event.tool_name
        if key in self._printed_tool_calls:
            return
        self._seen_process = True
        self._printed_tool_calls.add(key)
        self.console.print(f"[bold]Running[/bold] [cyan]{event.tool_name}[/cyan]")


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

    # Categorize commands into logical tables
    # 1. Session Management
    t_session = Table(show_header=False, box=None, padding=(0, 1))
    t_session.add_column("Command", style="bold #56B6C2", width=24)
    t_session.add_column("Desc", style="white")
    t_session.add_row("/new", "开启一轮全新的对话会话")
    t_session.add_row("/sessions", "列出所有已知的历史会话")
    t_session.add_row("/session <id>", "切换到指定的历史会话")
    t_session.add_row("/exit, /quit", "结束并退出当前会话")

    # 2. Model & Output
    t_model = Table(show_header=False, box=None, padding=(0, 1))
    t_model.add_column("Command", style="bold #56B6C2", width=24)
    t_model.add_column("Desc", style="white")
    t_model.add_row("/models", "列出当前 base URL 可用模型")
    t_model.add_row("/model [id]", "查看或切换当前模型")
    t_model.add_row("/config", "显示当前生效配置和来源")
    t_model.add_row("/stream [on|off]", "查看或切换流式输出模式")

    # 3. Memory & Facts
    t_memory = Table(show_header=False, box=None, padding=(0, 1))
    t_memory.add_column("Command", style="bold #56B6C2", width=24)
    t_memory.add_column("Desc", style="white")
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
    t_evolve.add_column("Command", style="bold #56B6C2", width=24)
    t_evolve.add_column("Desc", style="white")
    t_evolve.add_row("/evolve run", "基于当前会话优化并生成提示词")
    t_evolve.add_row("/evolve diff", "查看新提示词与旧版本的差异")
    t_evolve.add_row("/evolve apply", "应用刚刚生成的新提示词")
    t_evolve.add_row("/evolve rollback", "回滚到上一个提示词版本")

    # 5. Tools & System
    t_system = Table(show_header=False, box=None, padding=(0, 1))
    t_system.add_column("Command", style="bold #56B6C2", width=24)
    t_system.add_column("Desc", style="white")
    t_system.add_row("/tools", "列出所有注册的工具及其描述")
    t_system.add_row("/reload", "重新加载本地工具插件")
    t_system.add_row("/help", "显示本帮助信息")


    help_group = Group(
        "[bold #61AFEF]会话管理 (Session Management)[/]",
        t_session,
        "",
        "[bold #56B6C2]模型与控制 (Model & Output Control)[/]",
        t_model,
        "",
        "[bold #E5C07B]记忆与事实 (Memory & Facts Knowledge)[/]",
        t_memory,
        "",
        "[bold #C678DD]提示词优化 (Prompt Evolution)[/]",
        t_evolve,
        "",
        "[bold #98C379]工具与系统 (Tools & System Debug)[/]",
        t_system,
    )

    console.print(
        Panel(
            help_group,
            title="[bold #61AFEF]zzm-agent 控制台命令面板[/bold #61AFEF]",
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
    subtitle = Text("agentic coding console", style="italic dim #ABB2BF")
    
    # Info Table with emojis and custom colors
    info_table = Table.grid(padding=(0, 2))
    info_table.add_column(style="dim #ABB2BF", justify="right")
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
