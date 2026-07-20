from __future__ import annotations

import ast
import json
import os
import re
import time
from typing import Any

from zzm_agent.cli_support.commands import handle_slash
from zzm_agent.cli_support.rendering import build_terminal_renderer, build_prompt_session, read_repl_input, render_error_card, render_reply
from zzm_agent.core.model_stream import ModelStreamEvent

_SDK_ERROR_PAYLOAD_PATTERN = re.compile(r"-\s*(\{.*\})\s*$", re.DOTALL)

class _WorkingStatus:
    """Small animated status line rendered by Rich Live."""

    def __init__(
        self,
        runtime: dict[str, Any] | None = None,
        *,
        monotonic_fn: Any = time.monotonic,
    ) -> None:
        from rich.spinner import Spinner
        self.started_at = monotonic_fn()
        self.runtime = runtime
        self.spinner = Spinner("dots", style="bold #56B6C2")

    def update_runtime(self, runtime: dict[str, Any] | None) -> None:
        if runtime is not None:
            self.runtime = runtime

    def __rich_console__(self, console: Any, options: Any) -> Any:
        from rich.text import Text

        elapsed = time.monotonic() - self.started_at
        self.spinner.text = Text(f" Thinking... ({elapsed:.1f}s)", style="bold #56B6C2")
        yield self.spinner


def _build_working_footer(runtime: dict[str, Any] | None) -> Any | None:
    if not runtime:
        return None
    try:
        from rich.text import Text
    except ImportError:
        return None

    loop = runtime.get("loop")
    store = runtime.get("store")
    if not loop or not store:
        return None

    workspace_path = os.environ.get("ZZM_AGENT_WORKSPACE_ROOT", os.getcwd())
    context_window = getattr(loop, "last_context_window", {}) or {}
    context_limit = int(
        context_window.get("max_context_tokens", 0)
        or getattr(store, "max_context_tokens", 0)
        or 0
    )
    last_usage = getattr(loop, "last_turn_usage", None)
    context_used = getattr(last_usage, "prompt_tokens", 0) or 0

    footer = Text(" 💻 ", style="#777777")
    footer.append(str(workspace_path), style="dim #ABB2BF")
    footer.append(" │ 🤖 Model: ", style="#777777")
    footer.append(str(getattr(loop, "model", "")), style="bold #56B6C2")
    footer.append(" │ 🧠 Context: ", style="#777777")
    footer.append(f"{context_used}/{context_limit}", style="bold #98C379")
    return footer


def _start_working_status(
    console: Any,
    *,
    runtime: dict[str, Any] | None = None,
    reset_elapsed: bool = True,
    monotonic_fn: Any = time.monotonic,
) -> bool:
    if getattr(console, "_zzm_working_live", None) is not None:
        status = getattr(console, "_zzm_working_status", None)
        if status is not None:
            status.update_runtime(runtime)
        return True
    try:
        from rich.live import Live
    except ImportError:
        return False

    status = getattr(console, "_zzm_working_status", None)
    if reset_elapsed or status is None:
        status = _WorkingStatus(runtime, monotonic_fn=monotonic_fn)
        setattr(console, "_zzm_working_status", status)
    else:
        status.update_runtime(runtime)

    live = Live(status, console=console, refresh_per_second=12, transient=True)
    live.start()
    setattr(console, "_zzm_working_live", live)
    return True


def _stop_working_status(console: Any, *, clear_status: bool = False) -> bool:
    live = getattr(console, "_zzm_working_live", None)
    if live is None:
        return False
    try:
        live.stop()
    finally:
        setattr(console, "_zzm_working_live", None)
        if clear_status:
            setattr(console, "_zzm_working_status", None)
    return True


def _format_repl_exception(exc: Exception) -> str:
    """Return a concise user-facing error message for the interactive REPL."""
    text = str(exc).strip()
    payload = _extract_sdk_error_payload(text)
    if payload:
        error = payload.get("error")
        if isinstance(error, dict):
            message = str(error.get("message") or "模型接口请求失败")
            code = error.get("code")
            error_type = error.get("type")
            detail_parts = []
            if code:
                detail_parts.append(f"code: {code}")
            if error_type and error_type != code:
                detail_parts.append(f"type: {error_type}")
            detail = f" ({', '.join(detail_parts)})" if detail_parts else ""
            return f"模型接口请求失败：{message}{detail}"

    if text:
        return text
    return exc.__class__.__name__


def format_runtime_exception(
    exc: Exception,
    runtime: dict[str, Any] | None = None,
) -> str:
    """Add endpoint-aware hints for common OpenAI-compatible provider failures."""
    text = str(exc).strip()
    lower_text = text.lower()
    base_message = _format_repl_exception(exc)

    cfg = (runtime or {}).get("config") or {}
    model_cfg = cfg.get("model") or {}
    details = []
    base_url = str(model_cfg.get("base_url") or "").strip()
    model_name = str(model_cfg.get("model_name") or "").strip()
    if base_url:
        details.append(f"base_url={base_url}")
    if model_name:
        details.append(f"model={model_name}")
    endpoint_summary = f"；当前配置：{'，'.join(details)}" if details else ""

    if "404 page not found" in lower_text or "404 page not foun" in lower_text:
        return (
            "模型接口请求失败：404 page not found。通常是 `model.base_url` 配置错误，"
            "或者当前服务并不支持 `/chat/completions` 接口。"
            f"{endpoint_summary}"
        )

    if "response did not include choices" in lower_text:
        return (
            "模型接口请求失败：服务端返回了非标准的 OpenAI-compatible 响应，"
            "响应里没有 `choices` 字段。请优先检查 `model.base_url`、`model_name`，"
            "以及当前服务是否真的兼容 `/chat/completions`。"
            f"{endpoint_summary}"
        )

    if endpoint_summary and base_message != text:
        return f"{base_message}{endpoint_summary}"
    return base_message


def _extract_sdk_error_payload(text: str) -> dict[str, Any] | None:
    """Extract dict-like error payloads from OpenAI-compatible SDK exceptions."""
    match = _SDK_ERROR_PAYLOAD_PATTERN.search(text)
    if not match:
        return None
    raw_payload = match.group(1)
    try:
        payload = ast.literal_eval(raw_payload)
    except (SyntaxError, ValueError):
        try:
            payload = json.loads(raw_payload)
        except json.JSONDecodeError:
            return None
    return payload if isinstance(payload, dict) else None


def run_repl(
    runtime: dict[str, Any],
    *,
    build_prompt_session_fn: Any = build_prompt_session,
    read_repl_input_fn: Any = read_repl_input,
    monotonic_fn: Any = time.monotonic,
) -> int:
    """Run the interactive CLI loop using already-assembled runtime objects."""
    from zzm_agent.cli_support.rendering import render_welcome

    console = runtime["console"]
    registry = runtime["registry"]
    store = runtime["store"]
    optimizer = runtime["optimizer"]
    loop = runtime["loop"]
    query_engine = runtime.get("query_engine")
    observer = runtime.get("observer")
    if query_engine is None:
        raise RuntimeError("Interactive runtime requires QueryEngine.")
    prompt_session = build_prompt_session_fn(
        workspace=os.environ.get("ZZM_AGENT_WORKSPACE_ROOT", os.getcwd()),
        runtime=runtime
    )

    # Show professional welcome panel on startup
    render_welcome(
        console,
        session_id=store.session_id,
        model=loop.model,
        workspace=os.environ.get("ZZM_AGENT_WORKSPACE_ROOT", os.getcwd()),
        tool_count=len(registry.get_schemas()),
    )
    console.print()  # Add an extra newline before the prompt

    while True:
        try:
            user_input = read_repl_input_fn(console, prompt_session)
        except (KeyboardInterrupt, EOFError):
            console.print("\n[dim]Bye.[/dim]")
            return 0

        if not user_input:
            continue

        if user_input.startswith("/"):
            if not handle_slash(user_input, registry, store, optimizer, console, runtime=runtime):
                console.print(f"[red]Unknown command: {user_input}[/red]")
                setattr(console, "_zzm_last_turn_success", False)
            else:
                setattr(console, "_zzm_last_turn_success", True)
            continue

        try:
            console.print()
            stream_enabled = bool(runtime.get("stream", True))
            streamed = {"seen": False, "content": False}
            stream_renderer = build_terminal_renderer(console)

            def on_text_chunk(chunk: str) -> None:
                if not streamed["content"]:
                    _stop_working_status(console)
                streamed["seen"] = True
                streamed["content"] = True
                from zzm_agent.core.model_stream import ModelStreamEvent as _StreamEvent
                stream_renderer.render_event(_StreamEvent.content_delta(chunk))

            def on_stream_event(event: ModelStreamEvent) -> None:
                if not streamed["seen"] and stream_renderer.should_stop_working_status(event):
                    _stop_working_status(console)
                streamed["seen"] = True
                stream_renderer.render_event(event)

            if not stream_enabled:
                started = _start_working_status(console, runtime=runtime, monotonic_fn=monotonic_fn)
                try:
                    reply = query_engine.submit_message(
                        user_input,
                        stream=False,
                    ).reply
                finally:
                    if started:
                        _stop_working_status(console, clear_status=True)
            elif not _start_working_status(console, runtime=runtime, monotonic_fn=monotonic_fn):
                reply = query_engine.submit_message(
                    user_input,
                    stream=True,
                    on_stream_event=on_stream_event,
                ).reply
            else:
                try:
                    reply = query_engine.submit_message(
                        user_input,
                        stream=True,
                        on_stream_event=on_stream_event,
                    ).reply
                finally:
                    _stop_working_status(console, clear_status=True)

            if streamed["seen"]:
                stream_renderer.finish(reply)
            else:
                render_reply(console, reply)

            if streamed["seen"]:
                console.print()
                
            if observer is not None:
                observer.finish_turn(
                    loop.last_turn_usage,
                    loop.cumulative_usage,
                    context_window=loop.last_context_window,
                )
            setattr(console, "_zzm_last_turn_success", True)
        except Exception as exc:
            setattr(console, "_zzm_last_turn_success", False)
            if observer is not None:
                observer.stop()
            if runtime.get("debug"):
                console.print_exception()
            else:
                render_error_card(console, exc, runtime)
            console.print("[dim]Repl is still running. Fix the issue or press Ctrl+C to exit.[/dim]")
            continue


