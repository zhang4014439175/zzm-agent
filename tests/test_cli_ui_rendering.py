from __future__ import annotations

from rich.console import Console
from rich.markdown import Markdown

from tests.test_cli import DummyConsole
from zzm_agent.cli_support.ui.legacy import (
    MarkdownStreamRenderer,
    ZzmMarkdown,
    _is_light_terminal_background,
    _install_markdown_code_style_patch,
    build_console,
)


def test_build_console_adapts_to_light_and_dark_theme(monkeypatch):
    """验证显式主题覆盖会为浅色和深色终端选择不同的高对比标题色。"""
    monkeypatch.setenv("ZZM_AGENT_TERMINAL_THEME", "light")
    light_console = build_console()
    assert light_console.get_style("markdown.h1").color.name == "#0969da"

    monkeypatch.setenv("ZZM_AGENT_TERMINAL_THEME", "dark")
    dark_console = build_console()
    assert dark_console.get_style("markdown.h1").color.name == "#61afef"


def test_terminal_background_signal_overrides_windows_application_theme(monkeypatch):
    """验证终端自身报告白底时优先使用浅色方案，不被深色系统主题误导。"""
    monkeypatch.delenv("ZZM_AGENT_TERMINAL_THEME", raising=False)
    monkeypatch.setenv("COLORFGBG", "0;15")

    assert _is_light_terminal_background() is True


def test_light_terminal_command_block_uses_dark_foreground(monkeypatch):
    """验证 Agent 输出的命令代码块在白色背景上使用深色而不是白色文字。"""
    monkeypatch.setenv("ZZM_AGENT_TERMINAL_THEME", "light")
    console = build_console()

    segments = list(console.render(ZzmMarkdown("```powershell\nGet-ChildItem\n```")))
    command_segments = [segment for segment in segments if "Get-ChildItem" in segment.text]

    assert command_segments
    assert command_segments[0].style.color is not None
    assert command_segments[0].style.color.name in {"#0550ae", "#000000"}


def test_dark_detection_does_not_force_white_plain_command_text(monkeypatch):
    """验证背景误判为深色时纯文本命令仍继承终端前景，而不是固定为白色。"""
    monkeypatch.setenv("ZZM_AGENT_TERMINAL_THEME", "dark")
    console = build_console()

    segments = list(console.render(ZzmMarkdown("```text\nzzm-agent\n/mcp\n```")))
    command_segments = [segment for segment in segments if "zzm-agent" in segment.text]

    assert command_segments
    assert command_segments[0].style.color is None
    assert command_segments[0].style.dim is not True


def test_markdown_renders_tables_and_code_blocks():
    """验证 Markdown 表格和围栏代码块均能输出完整文本。"""
    _install_markdown_code_style_patch()
    console = Console(record=True, width=80)
    md = Markdown("| Col A | Col B |\n|---|---|\n| 1 | 2 |\n\n```python\nx = 1\n```")
    console.print(md)
    output = console.export_text()
    assert "Col A" in output
    assert "Col B" in output
    assert "x = 1" in output


def test_markdown_stream_renderer_does_not_split_inside_code_fence():
    """验证流式渲染不会在尚未闭合的代码围栏内部提前切块。"""
    console = DummyConsole()
    renderer = MarkdownStreamRenderer(console)
    ready, rest = renderer._split_ready_block("```python\na = 1\n\nb = 2\n```\n\nAfter code")
    assert "After code" not in ready
    assert rest == "After code"
