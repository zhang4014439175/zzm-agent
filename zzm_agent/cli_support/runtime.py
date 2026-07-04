from __future__ import annotations

import argparse
import json
import os
import re
import time
from pathlib import Path
from typing import Any

from zzm_agent.constants import TOOL_EVENTS_PATH
from zzm_agent.cli_support.commands import handle_slash
from zzm_agent.cli_support.observability import CliObserver
from zzm_agent.cli_support.rendering import (
    MarkdownStreamRenderer,
    build_prompt_session,
    build_console,
    read_repl_input,
    render_reply,
)
from zzm_agent.core.agent_loop import AgentLoop
from zzm_agent.core.model_metadata import resolve_model_context_limit
from zzm_agent.core.observability import ToolEvent, ToolEventCallback, ToolEventLogger
from zzm_agent.core.tool_registry import ToolRegistry, set_active_registry
from zzm_agent.evolution.optimizer import EvolutionOptimizer
from zzm_agent.memory.io import StorageCorruptionError
from zzm_agent.memory.store import MemoryStore
from zzm_agent.prompt.manager import PromptManager

CONFIG_PATH = Path("config.yaml")
_ENV_VALUE_PATTERN = re.compile(r"^\$\{([A-Za-z_][A-Za-z0-9_]*)(?::-(.*))?\}$")


class _WorkingStatus:
    """Small animated status line rendered by Rich Live."""

    _shades = ("#555555", "#777777", "#999999", "#bbbbbb", "#dddddd", "#ffffff")

    def __init__(self) -> None:
        self.started_at = time.monotonic()

    def __rich_console__(self, console: Any, options: Any) -> Any:
        from rich.text import Text

        word = "working"
        offset = int(time.monotonic() * 8) % (len(word) + len(self._shades))
        elapsed = time.monotonic() - self.started_at
        text = Text("\u2022 ", style="#777777")
        for index, char in enumerate(word):
            shade_index = max(0, len(self._shades) - 1 - abs(index - offset))
            text.append(char, style=self._shades[shade_index])
        text.append(f" {elapsed:.1f}s", style="#777777")
        yield text


def _start_working_status(console: Any) -> bool:
    if getattr(console, "_zzm_working_live", None) is not None:
        return True
    try:
        from rich.live import Live
    except ImportError:
        return False

    status = getattr(console, "_zzm_working_status", None)
    if status is None:
        status = _WorkingStatus()
        setattr(console, "_zzm_working_status", status)

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
    candidates: list[Path] = []
    if config_path:
        candidates.append(Path(config_path).expanduser())

    env_config = os.environ.get("ZZM_AGENT_CONFIG")
    if env_config:
        candidates.append(Path(env_config).expanduser())

    candidates.append(Path.cwd() / CONFIG_PATH)
    candidates.append(Path(__file__).resolve().parents[2] / CONFIG_PATH)

    for candidate in candidates:
        resolved = candidate.resolve()
        if resolved.exists():
            return resolved

    raise FileNotFoundError(
        "config.yaml not found. Use --config or set ZZM_AGENT_CONFIG."
    )


def _expand_env_value(value: Any) -> Any:
    """Expand ${VAR} and ${VAR:-default} placeholders in config values."""
    if isinstance(value, dict):
        return {key: _expand_env_value(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_expand_env_value(item) for item in value]
    if not isinstance(value, str):
        return value

    match = _ENV_VALUE_PATTERN.fullmatch(value.strip())
    if match is None:
        return value

    env_name, default = match.groups()
    return os.environ.get(env_name, default or "")


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

    path = resolve_config_path(config_path)
    with path.open(encoding="utf-8") as handle:
        cfg = _expand_env_value(yaml.safe_load(handle) or {})
    cfg["_config_path"] = str(path)
    cfg["_config_dir"] = str(path.parent)
    return cfg


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
                _start_working_status(console)
            return False
        if paused_working:
            _start_working_status(console)
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

    return {
        "client": client,
        "config": cfg,
        "console": console,
        "registry": registry,
        "store": store,
        "optimizer": optimizer,
        "loop": loop,
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
            continue

        try:
            console.print()
            stream_enabled = bool(runtime.get("stream", True))
            streamed = {"seen": False}
            stream_renderer = MarkdownStreamRenderer(console)

            def on_text_chunk(chunk: str) -> None:
                if not streamed["seen"]:
                    _stop_working_status(console)
                streamed["seen"] = True
                stream_renderer.push(chunk)

            if not stream_enabled:
                started = _start_working_status(console)
                try:
                    reply = loop.run(user_input, stream=False)
                finally:
                    if started:
                        _stop_working_status(console, clear_status=True)
            elif not _start_working_status(console):
                reply = loop.run(user_input, stream=True, on_text_chunk=on_text_chunk)
            else:
                try:
                    reply = loop.run(user_input, stream=True, on_text_chunk=on_text_chunk)
                finally:
                    _stop_working_status(console, clear_status=True)

            if streamed["seen"]:
                stream_renderer.flush()
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
        except Exception as exc:
            if observer is not None:
                observer.stop()
            if runtime.get("debug"):
                console.print_exception()
            else:
                console.print(f"[red]Error: {exc}[/red]")
            console.print("[dim]Repl is still running. Fix the issue or press Ctrl+C to exit.[/dim]")
            continue


def main(argv: list[str] | None = None) -> int:
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
