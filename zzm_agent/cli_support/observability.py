from __future__ import annotations

import difflib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from zzm_agent.core.observability import TokenUsage, ToolEvent


_FILE_MUTATION_TOOLS = {"file_edit", "write_file", "file_append"}


@dataclass
class _FileSnapshot:
    path: Path
    before: str


class CliObserver:
    """Render tool activity, file diffs, and usage summaries in the CLI."""

    def __init__(
        self,
        console: Any,
        workspace_root: str | Path,
        input_price_per_1m: float = 0.0,
        output_price_per_1m: float = 0.0,
    ):
        self.console = console
        self.workspace_root = Path(workspace_root).expanduser().resolve()
        self.input_price_per_1m = float(input_price_per_1m or 0.0)
        self.output_price_per_1m = float(output_price_per_1m or 0.0)
        self._events: dict[str, ToolEvent] = {}
        self._running: set[str] = set()
        self._live: Any = None
        self._snapshots: dict[str, _FileSnapshot] = {}
        self._diffs: list[tuple[Path, str]] = []
        self._printed_events: set[str] = set()
        self._paused_working_status = False

    def on_tool_start(self, event: ToolEvent) -> None:
        self._events[event.tool_call_id] = event
        self._running.add(event.tool_call_id)
        self._capture_file_snapshot(event)
        self._start_live()
        self._refresh_live()

    def on_tool_end(self, event: ToolEvent) -> None:
        self._events[event.tool_call_id] = event
        self._running.discard(event.tool_call_id)
        if event.status == "success":
            self._collect_file_diff(event)
        self._refresh_live()
        self._stop_live_if_idle()
        self._render_tool_summary(event)

    def on_tool_error(self, event: ToolEvent) -> None:
        self._events[event.tool_call_id] = event
        self._running.discard(event.tool_call_id)
        self._refresh_live()
        self._stop_live_if_idle()
        self._render_tool_summary(event)

    def finish_turn(
        self,
        turn_usage: TokenUsage,
        cumulative_usage: TokenUsage,
        context_window: dict[str, Any] | None = None,
    ) -> None:
        """Render end-of-turn observability panels and reset transient state."""
        self.stop()
        self.render_diffs()
        self._events.clear()
        self._snapshots.clear()
        self._diffs.clear()
        self._printed_events.clear()

    def stop(self) -> None:
        """Stop any active Rich live renderer."""
        if self._live is None:
            return
        try:
            self._live.stop()
        finally:
            self._live = None

    def render_diffs(self) -> None:
        """Render all file diffs collected during this turn."""
        if not self._diffs:
            return
        for path, diff_text in self._diffs:
            try:
                from rich.text import Text
            except ImportError:
                self.console.print(self._format_plain_edit_summary(path, diff_text))
                continue

            self.console.print(self._format_edit_summary(path, diff_text, Text))

    def render_usage(self, turn_usage: TokenUsage, cumulative_usage: TokenUsage) -> None:
        """Render token and cost usage for the current and cumulative session."""
        if not turn_usage.has_tokens() and not cumulative_usage.has_tokens():
            return

        try:
            from rich.table import Table
        except ImportError:
            self.console.print(
                "Usage: "
                f"turn={turn_usage.total_tokens} tokens, "
                f"session={cumulative_usage.total_tokens} tokens"
            )
            return

        table = Table(show_header=True, header_style="bold #61AFEF", box=None, padding=(0, 1))
        table.add_column("Scope", style="bold #56B6C2")
        table.add_column("Prompt", justify="right")
        table.add_column("Completion", justify="right")
        table.add_column("Total", justify="right")
        table.add_column("Cost", justify="right")
        table.add_column("Source", style="dim #ABB2BF")

        table.add_row(
            "Turn",
            str(turn_usage.prompt_tokens),
            str(turn_usage.completion_tokens),
            str(turn_usage.total_tokens),
            self._format_cost(turn_usage),
            turn_usage.source,
        )
        table.add_row(
            "Session",
            str(cumulative_usage.prompt_tokens),
            str(cumulative_usage.completion_tokens),
            str(cumulative_usage.total_tokens),
            self._format_cost(cumulative_usage),
            cumulative_usage.source,
        )
        self.console.print(table)

    def _start_live(self) -> None:
        if self._live is not None:
            return
        if getattr(self.console, "_zzm_prompt_footer_live", False):
            self._render_plain_tool_status()
            return
        if not hasattr(self.console, "set_live"):
            self._render_plain_tool_status()
            return
        try:
            from rich.live import Live
        except ImportError:
            self._render_plain_tool_status()
            return
        self._pause_working_status()
        self._live = Live(
            self._build_tool_panel(),
            console=self.console,
            refresh_per_second=4,
            transient=True,
        )
        self._live.start()

    def _refresh_live(self) -> None:
        if self._live is None:
            self._render_plain_tool_status()
            return
        self._live.update(self._build_tool_panel())

    def _stop_live_if_idle(self) -> None:
        if self._running:
            return
        self.stop()
        self._resume_working_status()

    def _pause_working_status(self) -> None:
        live = getattr(self.console, "_zzm_working_live", None)
        if live is None:
            return
        try:
            live.stop()
        finally:
            setattr(self.console, "_zzm_working_live", None)
            self._paused_working_status = True

    def _resume_working_status(self) -> None:
        if not self._paused_working_status:
            return
        if getattr(self.console, "_zzm_working_live", None) is not None:
            self._paused_working_status = False
            return
        status = getattr(self.console, "_zzm_working_status", None)
        if status is None:
            self._paused_working_status = False
            return
        try:
            from rich.live import Live
        except ImportError:
            self._paused_working_status = False
            return

        live = Live(status, console=self.console, refresh_per_second=12, transient=True)
        live.start()
        setattr(self.console, "_zzm_working_live", live)
        self._paused_working_status = False

    def _build_tool_panel(self) -> Any:
        try:
            from rich import box
            from rich.panel import Panel
            from rich.table import Table
        except ImportError:
            return ""

        table = Table(show_header=False, box=None, padding=(0, 1), expand=False)
        table.add_column("tool", style="default", no_wrap=True)
        table.add_column("status", style="default", no_wrap=True)
        table.add_column("args", style="default")
        table.add_column("details", style="default")
        table.add_column("time", justify="right", style="dim")

        for event in self._events.values():
            duration = ""
            if event.duration_ms is not None:
                duration = f"{event.duration_ms / 1000:.2f}s"
            table.add_row(
                event.tool_name,
                self._status_markup(event.status),
                self._format_args(event),
                self._format_details(event),
                duration,
            )
        return Panel(
            table,
            title="[dim]tools[/dim]",
            title_align="left",
            border_style="dim #6E7681",
            box=box.SIMPLE,
            padding=(0, 1),
        )

    def _render_plain_tool_status(self) -> None:
        if not self._events:
            return
        event = list(self._events.values())[-1]
        if event.tool_call_id in self._printed_events:
            return
        duration = ""
        if event.duration_ms is not None:
            duration = f" in {event.duration_ms / 1000:.2f}s"
        self.console.print(
            f"* Tool: {event.tool_name} {event.status}{duration} "
            f"{self._format_args(event)}"
        )
        self._printed_events.add(event.tool_call_id)

    def _render_tool_summary(self, event: ToolEvent) -> None:
        if event.tool_call_id in self._printed_events:
            return

        try:
            from rich.text import Text
        except ImportError:
            self._render_plain_tool_status()
            return

        duration = ""
        if event.duration_ms is not None:
            duration = f" {event.duration_ms / 1000:.2f}s"
        line = Text.assemble(
            ("\u2022Tool: ", "#56B6C2 bold"),
            (event.tool_name, "default"),
            (" ", "default"),
            (self._status_plain(event.status), self._status_style(event.status)),
            (duration, "default"),
        )
        args = self._format_args(event)
        details = self._format_details(event)
        if args:
            line.append(f"  {args}", style="default")
        if details and event.status != "success":
            line.append(f"  {details}", style="default")
        self.console.print(line)
        self.console.print()
        self._printed_events.add(event.tool_call_id)

    def _status_markup(self, status: str) -> str:
        if status == "success":
            return "done"
        if status == "error":
            return "[#CF222E]error[/]"
        if status == "denied":
            return "denied"
        return "running"

    def _status_plain(self, status: str) -> str:
        if status == "success":
            return "done"
        return status

    def _status_style(self, status: str) -> str:
        if status == "error":
            return "#CF222E"
        return "default"

    def _format_args(self, event: ToolEvent) -> str:
        if not event.arguments_summary:
            return ""
        parts = []
        for key, value in event.arguments_summary.items():
            parts.append(f"{key}={value!r}")
        rendered = ", ".join(parts)
        if len(rendered) <= 120:
            return rendered
        return f"{rendered[:120]}..."

    def _format_details(self, event: ToolEvent) -> str:
        detail = event.error_message or ""
        if len(detail) <= 140:
            return detail
        return f"{detail[:140]}..."

    def _format_cost(self, usage: TokenUsage) -> str:
        if self.input_price_per_1m <= 0 and self.output_price_per_1m <= 0:
            return "n/a"
        return (
            f"${usage.estimated_cost_usd(self.input_price_per_1m, self.output_price_per_1m):.6f}"
        )

    def _capture_file_snapshot(self, event: ToolEvent) -> None:
        path = self._event_path(event)
        if path is None:
            return
        self._snapshots[event.tool_call_id] = _FileSnapshot(
            path=path,
            before=self._read_text(path),
        )

    def _collect_file_diff(self, event: ToolEvent) -> None:
        snapshot = self._snapshots.get(event.tool_call_id)
        if snapshot is None:
            return
        after = self._read_text(snapshot.path)
        if after == snapshot.before:
            return

        rel_path = self._display_path(snapshot.path)
        diff = "".join(
            difflib.unified_diff(
                snapshot.before.splitlines(keepends=True),
                after.splitlines(keepends=True),
                fromfile=f"{rel_path} (before)",
                tofile=f"{rel_path} (after)",
                n=3,
            )
        )
        if len(diff) > 20000:
            diff = diff[:20000] + "\n... diff truncated ...\n"
        self._diffs.append((snapshot.path, diff))

    def _format_edit_summary(self, path: Path, diff_text: str, text_cls: Any) -> Any:
        rel_path = self._display_path(path)
        added, removed = self._count_diff_changes(diff_text)
        summary = text_cls.assemble(
            ("\u2022Edited: ", "bold #56B6C2"),
            (rel_path, "dim"),
            ("  (", "dim"),
            (f"+{added}", "#2EA043"),
            (" ", "dim"),
            (f"-{removed}", "#CF222E"),
            (")", "dim"),
        )
        preview = self._diff_preview_lines(diff_text)
        if not preview:
            return summary

        summary.append("\n")
        for style, line in preview:
            summary.append(line.ljust(96), style)
            summary.append("\n")
        summary.rstrip()
        return summary

    def _format_plain_edit_summary(self, path: Path, diff_text: str) -> str:
        rel_path = self._display_path(path)
        added, removed = self._count_diff_changes(diff_text)
        lines = [f"* Edited {rel_path} (+{added} -{removed})"]
        lines.extend(line for _, line in self._diff_preview_lines(diff_text))
        return "\n".join(lines)

    def _count_diff_changes(self, diff_text: str) -> tuple[int, int]:
        added = 0
        removed = 0
        for line in diff_text.splitlines():
            if line.startswith("+++") or line.startswith("---"):
                continue
            if line.startswith("+"):
                added += 1
            elif line.startswith("-"):
                removed += 1
        return added, removed

    def _diff_preview_lines(self, diff_text: str, max_lines: int = 12) -> list[tuple[str, str]]:
        preview: list[tuple[str, str]] = []
        for line in diff_text.splitlines():
            if line.startswith(("---", "+++", "@@")):
                continue
            if not line:
                continue

            style = "#7f8790"
            marker = " "
            content = line
            if line.startswith("+"):
                marker = "+"
                content = line[1:]
                style = "#b8d3bf on #2f5f43"
            elif line.startswith("-"):
                marker = "-"
                content = line[1:]
                style = "#ffd7d5 on #67060c"
            elif line.startswith(" "):
                content = line[1:]

            if not content.strip() and marker == " ":
                continue
            preview.append((style, f"  {marker} {content}"))
            if len(preview) >= max_lines:
                break

        total_lines = sum(
            1
            for line in diff_text.splitlines()
            if line and not line.startswith(("---", "+++", "@@"))
        )
        if total_lines > max_lines:
            preview.append(("bg:default #6E7681", f"  ... {total_lines - max_lines} more lines"))
        return preview

    def _event_path(self, event: ToolEvent) -> Path | None:
        base_name = event.tool_name.rsplit(".", 1)[-1]
        if base_name not in _FILE_MUTATION_TOOLS:
            return None
        raw_path = event.arguments_summary.get("path")
        if not isinstance(raw_path, str) or not raw_path:
            return None

        path = Path(raw_path).expanduser()
        candidate = path if path.is_absolute() else self.workspace_root / path
        resolved = candidate.resolve(strict=False)
        if not resolved.is_relative_to(self.workspace_root):
            return None
        return resolved

    def _read_text(self, path: Path) -> str:
        if not path.exists() or not path.is_file():
            return ""
        return path.read_text(encoding="utf-8", errors="replace")

    def _display_path(self, path: Path) -> str:
        try:
            return str(path.relative_to(self.workspace_root))
        except ValueError:
            return str(path)
