from __future__ import annotations

import argparse
import ast
import json
import os
import re
import time
from pathlib import Path
from typing import Any

from zzm_agent.constants import TOOL_EVENTS_PATH, ZZM_AGENT_DIR
from zzm_agent.cli_support.commands import handle_slash
from zzm_agent.cli_support.observability import CliObserver
from zzm_agent.cli_support.rendering import (
    build_terminal_renderer,
    build_prompt_session,
    build_console,
    read_repl_input,
    render_reply,
    render_error_card,
)
from zzm_agent.core.agent_loop import AgentLoop
from zzm_agent.core.config import ConfigManager
from zzm_agent.core.model_stream import ModelStreamEvent
from zzm_agent.core.model_metadata import resolve_model_context_limit
from zzm_agent.core.observability import ToolEvent, ToolEventCallback, ToolEventLogger
from zzm_agent.core.query_engine import QueryEngine
from zzm_agent.core.tool_registry import ToolRegistry, set_active_registry
from zzm_agent.evolution.optimizer import EvolutionOptimizer
from zzm_agent.memory.io import StorageCorruptionError
from zzm_agent.memory.store import MemoryStore
from zzm_agent.prompt.manager import PromptManager

CONFIG_PATH = Path("config.yaml")
_SDK_ERROR_PAYLOAD_PATTERN = re.compile(r"-\s*(\{.*\})\s*$", re.DOTALL)


class _WorkingStatus:
    """Small animated status line rendered by Rich Live."""

    def __init__(self, runtime: dict[str, Any] | None = None) -> None:
        from rich.spinner import Spinner
        self.started_at = time.monotonic()
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
        status = _WorkingStatus(runtime)
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


def _format_repl_exception_with_runtime(
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


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    import sys
    parser = argparse.ArgumentParser(description="zzm-agent")
    subparsers = parser.add_subparsers(dest="command", help="Available commands")
    
    repl_parser = subparsers.add_parser("repl", help="Start the interactive REPL loop (default)")
    repl_parser.add_argument("--session", dest="session_id", help="Resume or create a specific session id.")
    repl_parser.add_argument("--config", dest="config_path", help="Path to the YAML config file.")
    repl_parser.add_argument("--safe", action="store_true", help="Reserved for stricter confirmation policies. Medium/high-risk tools already require confirmation by default.")
    repl_parser.add_argument("--debug", action="store_true", help="Show full tracebacks for runtime errors.")
    
    eval_parser = subparsers.add_parser("eval", help="Run the evaluation suite")
    eval_parser.add_argument("--suite", choices=["replay", "smoke", "full"], required=True, help="Evaluation suite to run.")
    eval_parser.add_argument("--llm", action="store_true", help="Enable real LLM for smoke/full suites")
    eval_parser.add_argument("--config", dest="config_path", help="Path to the YAML config file.")

    if argv is None:
        argv = sys.argv[1:]
    if not argv or argv[0] not in ["repl", "eval"]:
        argv = ["repl"] + argv

    return parser.parse_args(argv)


def resolve_config_path(config_path: str | Path | None = None) -> Path:
    """Resolve the config path without assuming the current working directory."""
    manager = ConfigManager()
    sources = manager.resolve_default_sources(config_path)
    if not sources:
        raise FileNotFoundError(
            "config.yaml not found. Use --config or set ZZM_AGENT_CONFIG."
        )
    return sources[-1].path


def _config_bool(value: Any, default: bool = False) -> bool:
    """Parse permissive boolean config values while keeping YAML bools native."""
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"1", "true", "yes", "on"}:
            return True
        if normalized in {"0", "false", "no", "off"}:
            return False
    return default


def load_config(config_path: str | Path | None = None) -> dict[str, Any]:
    """
    Load the YAML configuration file used to bootstrap the CLI.

    Args:
        config_path: Path to the configuration file. Defaults to ``config.yaml``.

    Returns:
        Parsed configuration dictionary.

    Raises:
        RuntimeError: If PyYAML is not installed in the current interpreter.
    """
    try:
        from dotenv import load_dotenv
        load_dotenv()
    except ImportError:
        pass

    try:
        import yaml
    except ImportError as exc:
        raise RuntimeError("PyYAML is required to load config.yaml.") from exc

    _ = yaml
    return ConfigManager().load(explicit_path=config_path).config


def _resolve_plugin_dirs(cfg: dict[str, Any]) -> list[Path]:
    """Resolve plugin directories relative to the loaded config file."""
    config_dir = Path(cfg.get("_config_dir") or Path.cwd()).resolve()
    resolved_dirs: list[Path] = []
    for raw_dir in cfg.get("agent", {}).get("plugin_dirs", []):
        path = Path(str(raw_dir)).expanduser()
        if not path.is_absolute():
            path = config_dir / path
        resolved_dirs.append(path.resolve())
    return resolved_dirs


def build_tool_confirmation_callback(console: Any):
    """Return an interactive approval callback for tools that require it."""
    always_approved: set[str] = set()

    def confirm_tool(name: str, arguments: dict[str, Any], risk_level: str) -> bool:
        if name in always_approved:
            console.print(f"[dim]Using remembered approval for [cyan]{name}[/cyan].[/dim]")
            return True

        paused_working = _stop_working_status(console)
        _render_tool_approval_request(console, name, arguments, risk_level)
        try:
            answer = _ask_tool_approval_choice(console)
        except (KeyboardInterrupt, EOFError):
            if paused_working:
                _start_working_status(console, reset_elapsed=False)
            return False
        if paused_working:
            _start_working_status(console, reset_elapsed=False)
        if answer == "2":
            always_approved.add(name)
            return True
        return answer == "1"

    return confirm_tool


def _render_tool_approval_request(
    console: Any,
    name: str,
    arguments: dict[str, Any],
    risk_level: str,
) -> None:
    """Render a clear approval card before a risky tool runs."""
    try:
        from rich.console import Console
        from rich.text import Text
    except ImportError:
        console.print(f"Tool approval required ({risk_level} risk): {name}")
        console.print(_format_compact_arguments(arguments))
        console.print("[1] Allow once  [2] Always allow this tool this session  [3] Deny")
        return

    if not isinstance(console, Console):
        console.print(f"Tool approval required ({risk_level} risk): {name}")
        console.print(_format_compact_arguments(arguments))
        console.print("[1] Allow once  [2] Always allow this tool this session  [3] Deny")
        return

    body = Text.assemble(
        ("\u2022Approve: ", "#E5C07B bold"),
        (""),
        (risk_level.upper(), "default"),
        (" tool ", "default"),
        (name, "default"),
        (" args ", "default"),
        (_format_compact_arguments(arguments), "default"),
    )
    console.print(body)
    console.print()


def _format_compact_arguments(arguments: dict[str, Any], max_length: int = 160) -> str:
    rendered = json.dumps(
        arguments,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ": "),
    )
    if len(rendered) <= max_length:
        return rendered
    return rendered[: max_length - 3] + "..."


def _ask_tool_approval_choice(console: Any) -> str:
    """Ask for one of the explicit tool approval choices."""
    def ask_plain() -> str:
        choice = console.input("Approve [1/2/3] (1): ").strip()
        return choice if choice in {"1", "2", "3"} else "1"

    try:
        from prompt_toolkit import Application
        from prompt_toolkit.key_binding import KeyBindings
        from prompt_toolkit.layout import Layout
        from prompt_toolkit.layout.controls import FormattedTextControl
        from prompt_toolkit.layout.containers import Window
        from prompt_toolkit.styles import Style
    except ImportError:
        return ask_plain()

    choices = [
        ("Allow once", "1"),
        ("Always allow this tool this session", "2"),
        ("Deny", "3"),
    ]
    selected = {"index": 0}

    def get_fragments() -> list[tuple[str, str]]:
        fragments: list[tuple[str, str]] = []
        for index, (label, _value) in enumerate(choices):
            if index == selected["index"]:
                fragments.append(("class:selected", f">{label}\n"))
            else:
                fragments.append(("class:text", f" {label}\n"))
        return fragments

    bindings = KeyBindings()

    @bindings.add("up")
    @bindings.add("k")
    def _move_up(event: Any) -> None:
        selected["index"] = (selected["index"] - 1) % len(choices)

    @bindings.add("down")
    @bindings.add("j")
    def _move_down(event: Any) -> None:
        selected["index"] = (selected["index"] + 1) % len(choices)

    @bindings.add("enter")
    def _accept(event: Any) -> None:
        event.app.exit(result=choices[selected["index"]][1])

    @bindings.add("c-c")
    def _cancel(event: Any) -> None:
        event.app.exit(result="3")

    try:
        app = Application(
            layout=Layout(Window(FormattedTextControl(get_fragments), always_hide_cursor=True)),
            key_bindings=bindings,
            style=Style.from_dict({
                "text": "noreverse bg:default fg:default",
                "selected": "noreverse bg:default fg:#56B6C2",
            }),
            full_screen=False,
            erase_when_done=False,
        )
        answer = app.run()
    except Exception:
        return ask_plain()
    return answer or "1"



def _fanout_tool_callbacks(*callbacks: ToolEventCallback | None) -> ToolEventCallback:
    """Return one callback that forwards events to each configured observer."""
    active_callbacks = [callback for callback in callbacks if callback is not None]

    def fanout(event: ToolEvent) -> None:
        for callback in active_callbacks:
            callback(event)

    return fanout


def build_registry(cfg: dict[str, Any]) -> ToolRegistry:
    """
    Build a ToolRegistry and load every configured plugin directory into it.

    The global active registry is pointed at this instance before loading
    plugins so that the module-level ``@tool`` decorator registers functions
    into the same registry used by the agent loop.

    Args:
        cfg: Parsed application configuration.

    Returns:
        A registry populated with every discovered plugin tool.
    """
    registry = ToolRegistry()
    set_active_registry(registry)
    registry.configure_plugin_dirs(
        _resolve_plugin_dirs(cfg),
        plugin_config=cfg.get("plugins", {}),
    )
    registry.load_configured_plugins()

    return registry


def get_agent_loop_policy(cfg: dict[str, Any]) -> dict[str, int]:
    """Return configurable AgentLoop safety policy with stable defaults."""
    agent_cfg = cfg.get("agent", {})
    return {
        "max_tool_iterations": max(
            1,
            int(agent_cfg.get("max_tool_iterations", 20)),
        ),
        "duplicate_tool_call_limit": max(
            1,
            int(agent_cfg.get("duplicate_tool_call_limit", 3)),
        ),
        "max_tool_retries": max(
            0,
            int(agent_cfg.get("max_tool_retries", 1)),
        ),
    }


def build_runtime(args: argparse.Namespace, cfg: dict[str, Any]) -> dict[str, Any]:
    """Assemble the runtime objects used by the interactive CLI loop."""
    try:
        from openai import OpenAI
    except ImportError as exc:
        raise RuntimeError("OpenAI SDK is required to run zzm-agent.") from exc

    console = build_console()
    os.environ.setdefault("ZZM_AGENT_WORKSPACE_ROOT", str(Path.cwd().resolve()))

    api_key = (
        cfg["model"].get("api_key")
        or os.environ.get("ZZM_AGENT_API_KEY")
        or os.environ.get("OPENAI_API_KEY")
    )
    if not api_key:
        raise RuntimeError(
            "Model API key is required. Set model.api_key or ZZM_AGENT_API_KEY."
        )
    client = OpenAI(
        base_url=cfg["model"]["base_url"],
        api_key=api_key,
    )
    registry = build_registry(cfg)
    context_limit = resolve_model_context_limit(cfg)
    cfg.setdefault("runtime", {})["model_context_limit_source"] = context_limit.source
    store = MemoryStore(
        path=cfg["memory"]["path"],
        max_history=cfg["memory"]["max_history"],
        session_id=args.session_id,
        # This controls how many long-term memory items are injected per turn.
        retrieval_top_k=cfg["memory"].get("retrieval_top_k", 3),
        max_context_tokens=context_limit.tokens,
        compression_keep_recent=cfg["memory"].get("compression_keep_recent", 10),
        model_name=cfg["model"].get("model_name"),
        workspace_root=os.environ["ZZM_AGENT_WORKSPACE_ROOT"],
        instruction_filenames=tuple(
            cfg["memory"].get("instruction_files", ["AGENTS.md", "ZZM.md"])
        ),
        instruction_max_chars=cfg["memory"].get("instruction_max_chars", 8000),
        auto_memory_enabled=cfg["memory"].get("auto_memory_enabled", True),
    )
    optimizer = EvolutionOptimizer(
        client=client,
        model=cfg["model"]["model_name"],
        config_path=resolve_config_path(args.config_path),
        sample_size=cfg["evolution"]["sample_size"],
        history_versions=cfg["evolution"].get("history_versions", 5),
    )
    system_prompt = optimizer.get_current_prompt() or cfg["agent"]["system_prompt"]
    loop_policy = get_agent_loop_policy(cfg)
    model_cfg = cfg.get("model", {})
    workspace_root = Path(os.environ["ZZM_AGENT_WORKSPACE_ROOT"])
    observer = CliObserver(
        console=console,
        workspace_root=workspace_root,
        input_price_per_1m=float(model_cfg.get("input_price_per_1m", 0.0) or 0.0),
        output_price_per_1m=float(model_cfg.get("output_price_per_1m", 0.0) or 0.0),
    )
    tool_event_logger = ToolEventLogger(workspace_root / TOOL_EVENTS_PATH)
    prompt_manager = PromptManager(
        base_prompt=system_prompt,
        workspace_root=workspace_root,
        registry=registry,
    )
    loop = AgentLoop(
        client=client,
        model=cfg["model"]["model_name"],
        system_prompt=system_prompt,
        registry=registry,
        store=store,
        # Keep the agent loop aligned with MemoryStore's retrieval budget.
        memory_injection_limit=cfg["memory"].get("retrieval_top_k", 3),
        temperature=cfg["model"].get("temperature"),
        max_tokens=cfg["model"].get("max_tokens"),
        auto_approve=cfg["agent"].get("auto_approve", False),
        safe_mode=args.safe,
        confirm_tool=build_tool_confirmation_callback(console),
        max_tool_iterations=loop_policy["max_tool_iterations"],
        duplicate_tool_call_limit=loop_policy["duplicate_tool_call_limit"],
        max_tool_retries=loop_policy["max_tool_retries"],
        tool_choice=cfg.get("agent", {}).get("tool_choice", "auto"),
        on_tool_start=_fanout_tool_callbacks(observer.on_tool_start, tool_event_logger),
        on_tool_end=_fanout_tool_callbacks(observer.on_tool_end, tool_event_logger),
        on_tool_error=_fanout_tool_callbacks(observer.on_tool_error, tool_event_logger),
        prompt_manager=prompt_manager,
    )
    snapshot_path = workspace_root / ZZM_AGENT_DIR / "state" / f"{store.session_id}.json"
    query_engine = QueryEngine.with_snapshot_path(
        agent_loop=loop,
        snapshot_path=snapshot_path,
    )

    return {
        "client": client,
        "config": cfg,
        "console": console,
        "registry": registry,
        "store": store,
        "optimizer": optimizer,
        "loop": loop,
        "query_engine": query_engine,
        "prompt_manager": prompt_manager,
        "observer": observer,
        "model_context_limit_source": context_limit.source,
        "stream": _config_bool(cfg.get("agent", {}).get("stream"), default=True),
        "debug": bool(getattr(args, "debug", False)),
    }


def run_repl(runtime: dict[str, Any]) -> int:
    """Run the interactive CLI loop using already-assembled runtime objects."""
    from zzm_agent.cli_support.rendering import render_welcome

    console = runtime["console"]
    registry = runtime["registry"]
    store = runtime["store"]
    optimizer = runtime["optimizer"]
    loop = runtime["loop"]
    query_engine = runtime.get("query_engine")
    observer = runtime.get("observer")
    prompt_session = build_prompt_session(
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
            user_input = read_repl_input(console, prompt_session)
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
                if not streamed["seen"]:
                    _stop_working_status(console)
                streamed["seen"] = True
                stream_renderer.render_event(event)

            if not stream_enabled:
                started = _start_working_status(console, runtime=runtime)
                try:
                    if query_engine is not None:
                        reply = query_engine.submit_message(
                            user_input,
                            stream=False,
                        ).reply
                    else:
                        reply = loop.run(user_input, stream=False)
                finally:
                    if started:
                        _stop_working_status(console, clear_status=True)
            elif not _start_working_status(console, runtime=runtime):
                if query_engine is not None:
                    reply = query_engine.submit_message(
                        user_input,
                        stream=True,
                        on_stream_event=on_stream_event,
                    ).reply
                else:
                    reply = loop.run(user_input, stream=True, on_text_chunk=on_text_chunk)
            else:
                try:
                    if query_engine is not None:
                        reply = query_engine.submit_message(
                            user_input,
                            stream=True,
                            on_stream_event=on_stream_event,
                        ).reply
                    else:
                        reply = loop.run(user_input, stream=True, on_text_chunk=on_text_chunk)
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


def main(argv: list[str] | None = None) -> int:
    import sys
    # Configure stdout/stderr encoding to UTF-8
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

    args: argparse.Namespace | None = None
    try:
        args = parse_args(argv)
        cfg = load_config(args.config_path)
        
        if getattr(args, "command", "repl") == "eval":
            from zzm_agent.eval.runner import run_eval
            return run_eval(args.suite, args.llm, cfg)
            
        runtime = build_runtime(args, cfg)
        return run_repl(runtime)
    except StorageCorruptionError as exc:
        console = build_console()
        if args is not None and getattr(args, "debug", False):
            console.print_exception()
        else:
            console.print(f"[red]Storage corruption: {exc}[/red]")
        return 1
    except Exception:
        console = build_console()
        if args is not None and getattr(args, "debug", False):
            console.print_exception()
        else:
            console.print("[red]Unexpected error occurred. Re-run with --debug for traceback.[/red]")
        return 1
