#!/usr/bin/env python3
"""
Command-line entrypoint for zzm-agent.

The module keeps third-party imports inside functions where possible so the file
can still be imported in minimally provisioned environments when validating the
project structure.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

from zzm_agent.core.agent_loop import AgentLoop
from zzm_agent.core.tool_registry import ToolRegistry, set_active_registry
from zzm_agent.evolution.optimizer import EvolutionOptimizer
from zzm_agent.memory.store import MemoryStore

CONFIG_PATH = Path("config.yaml")


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
    except ImportError as exc:
        raise RuntimeError("Rich is required to run the CLI interface.") from exc

    return Console()


def render_reply(console: Any, reply: str) -> None:
    """
    Render an assistant reply using Rich Markdown when available.

    Args:
        console: Console-like object used for output.
        reply: Final assistant reply text to render.
    """
    try:
        from rich.markdown import Markdown
    except ImportError:
        console.print(reply)
        return

    console.print(Markdown(reply))


def stream_reply_chunk(console: Any, chunk: str) -> None:
    """
    Render streamed plain-text chunks as they arrive.

    Args:
        console: Console-like object used for output.
        chunk: Newly received text chunk.
    """
    console.print(chunk, end="")


def handle_slash(
    cmd: str,
    registry: ToolRegistry,
    store: MemoryStore,
    optimizer: EvolutionOptimizer,
    console: Any,
) -> bool:
    """
    Handle built-in slash commands.

    Args:
        cmd: Raw slash command entered by the user.
        registry: Registry used to introspect currently available tools.
        store: Persistent memory store used to show recent history.
        optimizer: Evolution optimizer invoked by ``/evolve``.
        console: Console-like object used to print feedback.

    Returns:
        ``True`` when the command was recognized and handled, otherwise ``False``.
    """
    command = cmd.strip()

    if command == "/tools":
        schemas = registry.get_schemas()
        if not schemas:
            console.print("[yellow]No tools registered.[/yellow]")
            return True

        for schema in schemas:
            function_meta = schema["function"]
            console.print(
                f"[cyan]{function_meta['name']}[/cyan]: {function_meta['description']}"
            )
        return True

    if command == "/memory":
        history = store.load_history()
        console.print(f"[yellow]{len(history)} messages in memory.[/yellow]")
        for message in history[-5:]:
            role = message.get("role", "?")
            content = str(message.get("content", ""))[:80]
            console.print(f"[cyan]{role}[/cyan]: {content}")
        return True

    if command == "/evolve":
        history = store.load_history()
        console.print("[yellow]Running evolution optimizer...[/yellow]")
        new_prompt = optimizer.optimize(history)
        if new_prompt:
            optimizer.apply(new_prompt)
            console.print("[green]System prompt updated.[/green]")
        else:
            console.print("[dim]Optimizer stub: no changes.[/dim]")
        return True

    if command == "/help":
        console.print("Commands: /tools  /memory  /evolve  /help  /exit")
        return True

    if command in {"/exit", "/quit"}:
        console.print("[dim]Bye.[/dim]")
        raise SystemExit(0)

    return False


def main() -> int:
    """
    Start the interactive REPL loop.

    Returns:
        Process exit code. ``0`` for normal termination.
    """
    try:
        from openai import OpenAI
    except ImportError as exc:
        raise RuntimeError("OpenAI SDK is required to run zzm-agent.") from exc

    cfg = load_config()
    console = build_console()

    client = OpenAI(
        base_url=cfg["model"]["base_url"],
        api_key=cfg["model"]["api_key"],
    )
    registry = build_registry(cfg)
    store = MemoryStore(
        path=cfg["memory"]["path"],
        max_history=cfg["memory"]["max_history"],
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
    )

    console.print("[bold green]zzm-agent[/bold green] started.")
    console.print(
        f"[dim]{len(registry.get_schemas())} tools loaded. Type /help for commands.[/dim]"
    )

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

            reply = loop.run(user_input, stream=False, on_text_chunk=on_text_chunk)
            if streamed["seen"]:
                # Streamed output has already been printed chunk-by-chunk above.
                console.print()
            else:
                render_reply(console, reply)
        except Exception as exc:
            console.print(f"[red]Error: {exc}[/red]")


if __name__ == "__main__":
    sys.exit(main())
