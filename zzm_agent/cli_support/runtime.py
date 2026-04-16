from __future__ import annotations

import argparse
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
from zzm_agent.memory.store import MemoryStore

CONFIG_PATH = Path("config.yaml")


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
    return parser.parse_args(argv)


def load_config(config_path: str | Path = CONFIG_PATH) -> dict[str, Any]:
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

    path = Path(config_path).expanduser().resolve()
    with path.open(encoding="utf-8") as handle:
        return yaml.safe_load(handle) or {}


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

    for plugin_dir in cfg.get("agent", {}).get("plugin_dirs", []):
        registry.load_plugin_dir(plugin_dir)

    return registry


def build_runtime(args: argparse.Namespace, cfg: dict[str, Any]) -> dict[str, Any]:
    """Assemble the runtime objects used by the interactive CLI loop."""
    try:
        from openai import OpenAI
    except ImportError as exc:
        raise RuntimeError("OpenAI SDK is required to run zzm-agent.") from exc

    console = build_console()
    client = OpenAI(
        base_url=cfg["model"]["base_url"],
        api_key=cfg["model"]["api_key"],
    )
    registry = build_registry(cfg)
    store = MemoryStore(
        path=cfg["memory"]["path"],
        max_history=cfg["memory"]["max_history"],
        session_id=args.session_id,
        # This controls how many long-term memory items are injected per turn.
        retrieval_top_k=cfg["memory"].get("retrieval_top_k", 3),
    )
    optimizer = EvolutionOptimizer(
        client=client,
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
    console = runtime["console"]
    registry = runtime["registry"]
    store = runtime["store"]
    optimizer = runtime["optimizer"]
    loop = runtime["loop"]

    console.print("[bold green]zzm-agent[/bold green] started.")
    console.print(
        f"[dim]{len(registry.get_schemas())} tools loaded. Type /help for commands.[/dim]"
    )
    console.print(f"[dim]Current session: {store.session_id}[/dim]")

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


def main() -> int:
    """
    Start the interactive REPL loop.

    Returns:
        Process exit code. ``0`` for normal termination.
    """
    args = parse_args()
    cfg = load_config()
    runtime = build_runtime(args, cfg)
    return run_repl(runtime)
