from __future__ import annotations

from typing import Any

from zzm_agent.core.tool_registry import ToolRegistry
from zzm_agent.evolution.optimizer import EvolutionOptimizer
from zzm_agent.memory.store import MemoryStore


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

    if command == "/sessions":
        # Session listing is read-only state inspection so operators can verify
        # which conversation the agent will continue before switching.
        sessions = store.list_sessions()
        if not sessions:
            console.print("[yellow]No sessions found.[/yellow]")
            return True

        for session in sessions:
            marker = "*" if session["id"] == store.session_id else " "
            console.print(
                f"{marker} [cyan]{session['id']}[/cyan]  "
                f"{session.get('name', session['id'])}  "
                f"[dim]{session.get('updated_at', '')}[/dim]"
            )
        return True

    if command.startswith("/session"):
        # Switching the active session changes which history file subsequent
        # user turns and `/memory` reads operate on.
        parts = command.split(maxsplit=1)
        if len(parts) != 2 or not parts[1].strip():
            console.print("[yellow]Usage: /session <id>[/yellow]")
            return True

        meta = store.switch_session(parts[1].strip())
        console.print(f"[green]Switched to session:[/green] [cyan]{meta['id']}[/cyan]")
        return True

    if command == "/new":
        # New sessions intentionally create a clean conversation boundary while
        # immediately making it the active target for future turns.
        meta = store.create_session()
        console.print(f"[green]Created session:[/green] [cyan]{meta['id']}[/cyan]")
        return True

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
        # `/memory` always reflects the currently selected session rather than a
        # global store, which is why the session id is displayed in the header.
        history = store.load_history()
        console.print(
            f"[yellow]{len(history)} messages in session {store.session_id}.[/yellow]"
        )
        for message in history[-5:]:
            role = message.get("role", "?")
            content = str(message.get("content", ""))[:80]
            console.print(f"[cyan]{role}[/cyan]: {content}")
        return True

    if command.startswith("/remember"):
        parts = command.split(maxsplit=1)
        if len(parts) != 2 or not parts[1].strip():
            console.print("[yellow]Usage: /remember <fact>[/yellow]")
            return True

        # `/remember` writes cross-session semantic memory, unlike `/memory`
        # which only inspects the active session transcript.
        entry = store.remember_fact(parts[1].strip())
        console.print(f"[green]Remembered:[/green] {entry['fact']}")
        return True

    if command.startswith("/forget"):
        parts = command.split(maxsplit=1)
        if len(parts) != 2 or not parts[1].strip():
            console.print("[yellow]Usage: /forget <keyword>[/yellow]")
            return True

        removed = store.forget_fact(parts[1].strip())
        console.print(f"[green]Forgot {removed} memory item(s).[/green]")
        return True

    if command == "/semantic":
        facts = store.list_semantic_facts()
        if not facts:
            console.print("[yellow]No long-term memories found.[/yellow]")
            return True

        console.print(
            f"[yellow]{len(facts)} long-term memories.[/yellow]"
        )
        for index, fact in enumerate(facts, start=1):
            console.print(f"[cyan]{index}.[/cyan] {fact}")
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
        console.print(
            "Commands: /sessions  /session <id>  /new  /tools  /memory  "
            "/remember <fact>  /forget <keyword>  /semantic  /evolve  /help  /exit"
        )
        return True

    if command in {"/exit", "/quit"}:
        console.print("[dim]Bye.[/dim]")
        raise SystemExit(0)

    return False
