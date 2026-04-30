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

    if command == "/reload":
        changes = registry.reload_plugins()
        total_changes = sum(len(items) for items in changes.values())
        if total_changes == 0:
            console.print("[dim]Plugins reloaded. No tool changes detected.[/dim]")
            return True

        console.print(
            "[green]Plugins reloaded.[/green] "
            f"Added {len(changes['added'])}, removed {len(changes['removed'])}, "
            f"updated {len(changes['updated'])} tools."
        )
        for label in ("added", "removed", "updated"):
            if not changes[label]:
                continue
            console.print(
                f"[cyan]{label}[/cyan]: " + ", ".join(changes[label])
            )
        return True

    if command == "/memory":
        # `/memory` always reflects the currently selected session rather than a
        # global store, which is why the session id is displayed in the header.
        history = store.load_history()
        preview = store.preview_context_window()
        console.print(
            f"[yellow]{len(history)} messages in session {store.session_id}.[/yellow]"
        )
        console.print(
            "[dim]"
            f"Estimated history tokens: {preview['raw_tokens']}/{preview['budget_tokens']} "
            f"via {store.token_count_source()}."
            "[/dim]"
        )
        if preview["applied"]:
            console.print(
                "[yellow]"
                "Context compression active. "
                f"Kept {preview['kept_recent_count']} raw messages "
                f"using {preview['compression_strategy']} strategy."
                "[/yellow]"
            )
            if preview["summary"]:
                console.print(f"[cyan]summary[/cyan]: {preview['summary']}")
        else:
            console.print("[dim]Context compression inactive.[/dim]")
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

    if command.startswith("/search"):
        parts = command.split(maxsplit=1)
        if len(parts) != 2 or not parts[1].strip():
            console.print("[yellow]Usage: /search <keyword>[/yellow]")
            return True

        keyword = parts[1].strip()
        results = store.search_memories(keyword)
        semantic = results["semantic"]
        episodic = results["episodic"]
        if not semantic and not episodic:
            console.print(f"[yellow]No memory matches for:[/yellow] {keyword}")
            return True

        console.print(
            "[yellow]"
            f"Memory matches for '{keyword}': {len(semantic)} semantic, {len(episodic)} episodic."
            "[/yellow]"
        )
        for index, entry in enumerate(semantic, start=1):
            console.print(f"[cyan]semantic {index}.[/cyan] {entry['fact']}")
        for index, entry in enumerate(episodic, start=1):
            summary = entry.get("summary", "")
            session_id = entry.get("session_id", "unknown-session")
            console.print(f"[cyan]episodic {index}.[/cyan] {session_id}: {summary}")
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

    if command in {"/evolve", "/evolve run"}:
        # Candidate generation is intentionally separated from apply so an
        # optimizer run cannot silently mutate the active system prompt.
        history = store.load_history()
        console.print("[yellow]Running evolution candidate generation...[/yellow]")
        candidate = optimizer.run(history)
        if not candidate:
            console.print("[dim]No prompt candidate generated.[/dim]")
            return True

        console.print(
            "[green]Prompt candidate generated:[/green] "
            f"[cyan]{candidate['id']}[/cyan]"
        )
        if candidate.get("rationale"):
            console.print(f"[dim]{candidate['rationale']}[/dim]")
        console.print("[dim]Review with /evolve diff, apply with /evolve apply.[/dim]")
        return True

    if command == "/evolve status":
        # Show the most recent evaluation record stored in evaluations.json
        latest = optimizer.get_latest_evaluation()
        if not latest:
            # If no evaluations exist, try to run one on the current history
            history = store.load_history()
            if not history:
                console.print("[yellow]No history available to evaluate.[/yellow]")
                return True
            
            console.print("[yellow]No prior evaluations found. Evaluating current history...[/yellow]")
            latest = optimizer.evaluate(history)
            if not latest:
                console.print("[red]Evaluation failed.[/red]")
                return True

        console.print("[bold blue]Latest Evolution Status[/bold blue]")
        console.print(f"Timestamp: [dim]{latest.get('timestamp', 'unknown')}[/dim]")
        console.print(f"Relevance: [green]{latest.get('relevance_score', 0)}/10[/green]")
        
        tool_score = latest.get('tool_usage_score')
        tool_display = f"[green]{tool_score}/10[/green]" if tool_score is not None else "[dim]N/A[/dim]"
        console.print(f"Tool Usage: {tool_display}")
        
        console.print(f"Conciseness: [green]{latest.get('conciseness_score', 0)}/10[/green]")
        console.print(f"Reasoning: {latest.get('reasoning', 'N/A')}")
        console.print(f"Conclusion: {latest.get('conclusion', 'N/A')}")
        return True

    if command.startswith("/evolve diff"):
        parts = command.split(maxsplit=2)
        candidate_id = parts[2].strip() if len(parts) == 3 else None
        diff = optimizer.diff(candidate_id)
        if not diff:
            console.print("[yellow]No prompt candidate available for diff.[/yellow]")
            return True

        console.print(diff)
        return True

    if command.startswith("/evolve apply"):
        parts = command.split(maxsplit=2)
        candidate_id = parts[2].strip() if len(parts) == 3 else None
        candidate = optimizer.apply_candidate(candidate_id)
        if not candidate:
            console.print("[yellow]No prompt candidate available to apply.[/yellow]")
            return True

        console.print(
            "[green]Applied prompt candidate as active prompt:[/green] "
            f"[cyan]{candidate['id']}[/cyan]"
        )
        return True

    if command == "/evolve rollback":
        restored = optimizer.rollback()
        if not restored:
            console.print("[yellow]No prompt history available to roll back.[/yellow]")
            return True

        console.print(
            "[green]Rolled back active prompt from history:[/green] "
            f"[cyan]{restored['id']}[/cyan]"
        )
        return True

    if command == "/help":
        from zzm_agent.cli_support.rendering import render_help
        render_help(console)
        return True

    if command in {"/exit", "/quit"}:
        console.print("[dim]Bye.[/dim]")
        raise SystemExit(0)

    return False
