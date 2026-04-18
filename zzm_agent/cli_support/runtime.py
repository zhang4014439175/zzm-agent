from __future__ import annotations

import argparse
import json
import os
import re
from pathlib import Path
from typing import Any

from zzm_agent.cli_support.commands import handle_slash
from zzm_agent.cli_support.rendering import (
    build_console,
    render_reply,
    stream_reply_chunk,
)
from zzm_agent.core.agent_loop import AgentLoop
from zzm_agent.core.tool_registry import ToolRegistry, set_active_registry
from zzm_agent.evolution.optimizer import EvolutionOptimizer
from zzm_agent.memory.io import StorageCorruptionError
from zzm_agent.memory.store import MemoryStore

CONFIG_PATH = Path("config.yaml")
_ENV_VALUE_PATTERN = re.compile(r"^\$\{([A-Za-z_][A-Za-z0-9_]*)(?::-(.*))?\}$")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """
    Parse bootstrap CLI flags before the interactive loop starts.

    Args:
        argv: Optional argument vector for tests and embedding.

    Returns:
        Parsed CLI arguments namespace.
    """
    # Keep CLI bootstrap flags centralized so session selection happens
    # before config loading and REPL startup.
    parser = argparse.ArgumentParser(description="zzm-agent")
    parser.add_argument(
        "--session",
        dest="session_id",
        # Reuse an existing session when present; otherwise create it so the
        # rest of the runtime can treat explicit session selection uniformly.
        help="Resume or create a specific session id.",
    )
    parser.add_argument(
        "--config",
        dest="config_path",
        help="Path to the YAML config file.",
    )
    parser.add_argument(
        "--safe",
        action="store_true",
        help="Require confirmation for medium-risk tools in addition to high-risk tools.",
    )
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
        import yaml
    except ImportError as exc:
        raise RuntimeError("PyYAML is required to load config.yaml.") from exc

    path = resolve_config_path(config_path)
    with path.open(encoding="utf-8") as handle:
        return _expand_env_value(yaml.safe_load(handle) or {})


def build_tool_confirmation_callback(console: Any):
    """Return an interactive approval callback for tools that require it."""
    def confirm_tool(name: str, arguments: dict[str, Any], risk_level: str) -> bool:
        rendered_args = json.dumps(arguments, ensure_ascii=False, sort_keys=True)
        prompt = (
            f"[yellow]Approve {risk_level}-risk tool [cyan]{name}[/cyan] "
            f"with args {rendered_args}? [y/N] [/yellow]"
        )
        try:
            answer = console.input(prompt).strip().lower()
        except (KeyboardInterrupt, EOFError):
            return False
        return answer in {"y", "yes"}

    return confirm_tool


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
    registry.configure_plugin_dirs(cfg.get("agent", {}).get("plugin_dirs", []))
    registry.load_configured_plugins()

    return registry


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
    store = MemoryStore(
        path=cfg["memory"]["path"],
        max_history=cfg["memory"]["max_history"],
        session_id=args.session_id,
        # This controls how many long-term memory items are injected per turn.
        retrieval_top_k=cfg["memory"].get("retrieval_top_k", 3),
        max_context_tokens=cfg["memory"].get("max_context_tokens", 8000),
        compression_keep_recent=cfg["memory"].get("compression_keep_recent", 10),
    )
    optimizer = EvolutionOptimizer(
        client=client,
        model=cfg["model"]["model_name"],
        config_path=CONFIG_PATH,
        sample_size=cfg["evolution"]["sample_size"],
    )
    loop = AgentLoop(
        client=client,
        model=cfg["model"]["model_name"],
        system_prompt=cfg["agent"]["system_prompt"],
        registry=registry,
        store=store,
        # Keep the agent loop aligned with MemoryStore's retrieval budget.
        memory_injection_limit=cfg["memory"].get("retrieval_top_k", 3),
        temperature=cfg["model"].get("temperature"),
        max_tokens=cfg["model"].get("max_tokens"),
        auto_approve=cfg["agent"].get("auto_approve", False),
        safe_mode=args.safe,
        confirm_tool=build_tool_confirmation_callback(console),
    )

    return {
        "console": console,
        "registry": registry,
        "store": store,
        "optimizer": optimizer,
        "loop": loop,
    }


def run_repl(runtime: dict[str, Any]) -> int:
    """Run the interactive CLI loop using already-assembled runtime objects."""
    from zzm_agent.cli_support.rendering import render_welcome

    console = runtime["console"]
    registry = runtime["registry"]
    store = runtime["store"]
    optimizer = runtime["optimizer"]
    loop = runtime["loop"]

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
            user_input = console.input("[bold blue]you>[/bold blue] ").strip()
        except (KeyboardInterrupt, EOFError):
            console.print("\n[dim]Bye.[/dim]")
            return 0

        if not user_input:
            continue

        if user_input.startswith("/"):
            if not handle_slash(user_input, registry, store, optimizer, console):
                console.print(f"[red]Unknown command: {user_input}[/red]")
            continue

        try:
            streamed = {"seen": False}

            def on_text_chunk(chunk: str) -> None:
                streamed["seen"] = True
                stream_reply_chunk(console, chunk)

            reply = loop.run(user_input, stream=True, on_text_chunk=on_text_chunk)
            if streamed["seen"]:
                # Streamed output has already been printed chunk-by-chunk above.
                console.print()
            else:
                render_reply(console, reply)
        except Exception as exc:
            console.print(f"[red]Error: {exc}[/red]")


def main(argv: list[str] | None = None) -> int:
    """
    Start the interactive REPL loop.

    Returns:
        Process exit code. ``0`` for normal termination.
    """
    try:
        args = parse_args(argv)
        cfg = load_config(args.config_path)
        runtime = build_runtime(args, cfg)
        return run_repl(runtime)
    except StorageCorruptionError as exc:
        console = build_console()
        console.print(f"[red]Storage corruption: {exc}[/red]")
        return 1
