from __future__ import annotations

import re
from typing import Any

from zzm_agent.core.model_metadata import resolve_model_context_limit
from zzm_agent.core.tool_registry import ToolRegistry
from zzm_agent.evolution.optimizer import EvolutionOptimizer
from zzm_agent.memory.store import MemoryStore
from zzm_agent.memory.token_counter import TokenCounter
from zzm_agent.cli_support.rendering import render_notification


def handle_slash(
    cmd: str,
    registry: ToolRegistry,
    store: MemoryStore,
    optimizer: EvolutionOptimizer,
    console: Any,
    runtime: dict[str, Any] | None = None,
) -> bool:
    """
    Handle built-in slash commands.

    Args:
        cmd: Raw slash command entered by the user.
        registry: Registry used to introspect currently available tools.
        store: Persistent memory store used to show recent history.
        optimizer: Evolution optimizer invoked by ``/evolve``.
        console: Console-like object used to print feedback.
        runtime: Optional live runtime state for commands that adjust current REPL behavior.

    Returns:
        ``True`` when the command was recognized and handled, otherwise ``False``.
    """
    command = cmd.strip()

    if command == "/sessions":
        sessions = store.list_sessions()
        if not sessions:
            if console.__class__.__name__ == "Console":
                render_notification(console, "未找到历史会话。", "warning")
            else:
                console.print("[yellow]No sessions found.[/yellow]")
            return True

        if console.__class__.__name__ == "Console":
            try:
                from rich.table import Table
                from rich import box
                table = Table(show_header=True, header_style="bold #61AFEF", box=box.ROUNDED, border_style="#3B4252", padding=(0, 2))
                table.add_column("状态", justify="center", width=8)
                table.add_column("会话 ID", style="bold #56B6C2")
                table.add_column("会话名称 (Name)", style="white")
                table.add_column("最后活跃时间 (Updated At)", style="dim #ABB2BF")

                for session in sessions:
                    is_active = session["id"] == store.session_id
                    status = "[#98C379]● 活动[/]" if is_active else "[dim]● 历史[/]"
                    table.add_row(
                        status,
                        session["id"],
                        session.get("name", session["id"]),
                        session.get("updated_at", "")
                    )
                console.print(table)
            except Exception:
                for session in sessions:
                    marker = "*" if session["id"] == store.session_id else " "
                    console.print(
                        f"{marker} [cyan]{session['id']}[/cyan]  "
                        f"{session.get('name', session['id'])}  "
                        f"[dim]{session.get('updated_at', '')}[/dim]"
                    )
        else:
            for session in sessions:
                marker = "*" if session["id"] == store.session_id else " "
                console.print(
                    f"{marker} [cyan]{session['id']}[/cyan]  "
                    f"{session.get('name', session['id'])}  "
                    f"[dim]{session.get('updated_at', '')}[/dim]"
                )
        return True

    if command.startswith("/session"):
        parts = command.split(maxsplit=1)
        if len(parts) != 2 or not parts[1].strip():
            if console.__class__.__name__ == "Console":
                render_notification(console, "命令使用方法: /session <id>", "warning")
            else:
                console.print("[yellow]Usage: /session <id>[/yellow]")
            return True

        meta = store.switch_session(parts[1].strip())
        if console.__class__.__name__ == "Console":
            render_notification(console, f"已成功切换到会话：[cyan]{meta['id']}[/cyan]", "success")
        else:
            console.print(f"[green]Switched to session:[/green] [cyan]{meta['id']}[/cyan]")
        return True

    if command == "/new":
        meta = store.create_session()
        if console.__class__.__name__ == "Console":
            render_notification(console, f"已成功创建全新会话：[cyan]{meta['id']}[/cyan]", "success")
        else:
            console.print(f"[green]Created session:[/green] [cyan]{meta['id']}[/cyan]")
        return True

    if command == "/tools":
        schemas = registry.get_schemas()
        if not schemas:
            if console.__class__.__name__ == "Console":
                render_notification(console, "当前未注册任何工具插件。", "warning")
            else:
                console.print("[yellow]No tools registered.[/yellow]")
            return True

        if console.__class__.__name__ == "Console":
            try:
                from rich.table import Table
                from rich import box
                table = Table(show_header=True, header_style="bold #61AFEF", box=box.ROUNDED, border_style="#3B4252", padding=(0, 2))
                table.add_column("工具名称 (Tool Name)", style="bold #56B6C2", no_wrap=True)
                table.add_column("风险评级 (Risk)", justify="center", width=12)
                table.add_column("工具用途描述 (Description)", style="white")

                for schema in schemas:
                    function_meta = schema["function"]
                    name = function_meta["name"]
                    desc = function_meta["description"]
                    
                    # Determine risk level from schema or estimate by name
                    risk_level = schema.get("risk_level", "").lower()
                    if not risk_level:
                        name_lower = name.lower()
                        if "run" in name_lower or "command" in name_lower or "shell" in name_lower:
                            risk_level = "high"
                        elif "edit" in name_lower or "write" in name_lower or "append" in name_lower or "delete" in name_lower:
                            risk_level = "medium"
                        else:
                            risk_level = "low"

                    if risk_level == "high":
                        risk_markup = "[bold #CF222E]High (高)[/]"
                    elif risk_level == "low":
                        risk_markup = "[bold #98C379]Low (低)[/]"
                    else:
                        risk_markup = "[bold #E5C07B]Medium (中)[/]"

                    table.add_row(name, risk_markup, desc)
                console.print(table)
            except Exception:
                for schema in schemas:
                    function_meta = schema["function"]
                    console.print(
                        f"[cyan]{function_meta['name']}[/cyan]: {function_meta['description']}"
                    )
        else:
            for schema in schemas:
                function_meta = schema["function"]
                console.print(
                    f"[cyan]{function_meta['name']}[/cyan]: {function_meta['description']}"
                )
        return True

    if command.startswith("/models"):
        if runtime is None:
            if console.__class__.__name__ == "Console":
                render_notification(console, "当前上下文中无法获取模型列表。", "warning")
            else:
                console.print("[yellow]Model listing is unavailable in this context.[/yellow]")
            return True

        parts = command.split(maxsplit=1)
        name_filter = parts[1].strip() if len(parts) == 2 else ""
        models = _list_runtime_models(runtime)
        if models is None:
            if console.__class__.__name__ == "Console":
                render_notification(console, "获取模型列表失败，请检查模型 API 配置或网络连接。", "error")
            else:
                console.print("[red]Failed to fetch models from the configured base_url.[/red]")
            return True
        if name_filter:
            models = [model_id for model_id in models if name_filter in model_id]
        if not models:
            if name_filter:
                if console.__class__.__name__ == "Console":
                    render_notification(console, f"未匹配到含有关键词 '{name_filter}' 的模型。", "warning")
                else:
                    console.print("[yellow]" f"No models matched name filter: {name_filter}" "[/yellow]")
            else:
                if console.__class__.__name__ == "Console":
                    render_notification(console, "配置的服务端未返回任何可用模型。", "warning")
                else:
                    console.print("[yellow]No models returned by the configured base_url.[/yellow]")
            return True

        current_model = str(getattr(runtime.get("loop"), "model", ""))
        if console.__class__.__name__ == "Console":
            try:
                from rich.table import Table
                from rich import box
                table = Table(show_header=True, header_style="bold #61AFEF", box=box.ROUNDED, border_style="#3B4252", padding=(0, 2))
                table.add_column("状态", justify="center", width=12)
                table.add_column("可用模型 ID (Model ID)", style="bold #56B6C2")

                for model_id in models:
                    is_active = model_id == current_model
                    status = "[bold #98C379]● 活动 (Active)[/]" if is_active else "[dim]● 可用[/]"
                    table.add_row(status, model_id)
                console.print(table)
            except Exception:
                console.print(f"[yellow]{len(models)} model(s) available.[/yellow]")
                for model_id in models:
                    marker = "*" if model_id == current_model else " "
                    console.print(f"{marker} [cyan]{model_id}[/cyan]")
        else:
            console.print(f"[yellow]{len(models)} model(s) available.[/yellow]")
            for model_id in models:
                marker = "*" if model_id == current_model else " "
                console.print(f"{marker} [cyan]{model_id}[/cyan]")
        return True

    if command.startswith("/model"):
        if runtime is None:
            if console.__class__.__name__ == "Console":
                render_notification(console, "当前上下文中无法切换模型。", "warning")
            else:
                console.print("[yellow]Model switching is unavailable in this context.[/yellow]")
            return True

        parts = command.split(maxsplit=1)
        loop = runtime.get("loop")
        if len(parts) == 1 or not parts[1].strip():
            current_model = getattr(loop, "model", "unknown")
            if console.__class__.__name__ == "Console":
                render_notification(console, f"当前活跃模型为：[bold #56B6C2]{current_model}[/bold #56B6C2]", "system")
                console.print("[dim]提示：使用 /models 列出模型，/model <id> 切换模型。[/dim]")
            else:
                console.print(f"[cyan]Current model:[/cyan] {current_model}")
                console.print("[dim]Use /models to list models, /model <id> to switch.[/dim]")
            return True

        model_id = parts[1].strip()
        models = _list_runtime_models(runtime)
        if models is None:
            if console.__class__.__name__ == "Console":
                render_notification(console, "获取可用模型列表失败，无法验证模型有效性。", "error")
            else:
                console.print("[red]Failed to fetch models from the configured base_url.[/red]")
            return True
        if model_id not in models:
            if console.__class__.__name__ == "Console":
                render_notification(console, f"配置的服务中未找到模型：{model_id}。请使用 /models 查看可用列表。", "warning")
            else:
                console.print(f"[yellow]Model not found:[/yellow] {model_id}")
                console.print("[dim]Use /models to list available model ids.[/dim]")
            return True

        _switch_runtime_model(runtime, model_id)
        context_limit = getattr(runtime.get("store"), "max_context_tokens", 0)
        if console.__class__.__name__ == "Console":
            render_notification(
                console,
                f"已切换当前模型为：[bold #98C379]{model_id}[/bold #98C379] [dim](Context: {context_limit})[/dim]",
                "success"
            )
        else:
            console.print(
                "[green]Switched model:[/green] "
                f"[cyan]{model_id}[/cyan] "
                f"[dim](context {context_limit})[/dim]"
            )
        return True

    if command == "/reload":
        changes = registry.reload_plugins()
        total_changes = sum(len(items) for items in changes.values())
        if total_changes == 0:
            if console.__class__.__name__ == "Console":
                render_notification(console, "插件已重新加载，未检测到任何工具变动。", "system")
            else:
                console.print("[dim]Plugins reloaded. No tool changes detected.[/dim]")
            return True

        if console.__class__.__name__ == "Console":
            msg = (
                f"插件已成功重新加载！"
                f"新增 {len(changes['added'])}，移除 {len(changes['removed'])}，"
                f"更新 {len(changes['updated'])} 个工具。"
            )
            render_notification(console, msg, "success")
        else:
            console.print(
                "[green]Plugins reloaded.[/green] "
                f"Added {len(changes['added'])}, removed {len(changes['removed'])}, "
                f"updated {len(changes['updated'])} tools."
            )
        for label in ("added", "removed", "updated"):
            if not changes[label]:
                continue
            console.print(
                f"  [cyan]{label}[/cyan]: " + ", ".join(changes[label])
            )
        return True

    if command == "/memory":
        history = store.load_history()
        preview = store.preview_context_window()
        if console.__class__.__name__ == "Console":
            try:
                from rich.panel import Panel
                from rich.console import Group
                from rich.text import Text
                from rich import box
                
                info_text = Text()
                info_text.append(f"📊 当前会话 ({store.session_id}) 共有 ", style="default")
                info_text.append(f"{len(history)}", style="bold #56B6C2")
                info_text.append(" 条历史消息。\n", style="default")
                info_text.append("🧠 上下文 Token 估算：", style="default")
                info_text.append(f"{preview['raw_tokens']}", style="bold #98C379")
                info_text.append(f" / {preview['budget_tokens']}", style="dim")
                info_text.append(f" ({store.token_count_source()})", style="dim italic")
                
                if preview["applied"]:
                    info_text.append("\n⚡ 启用上下文压缩：", style="bold #E5C07B")
                    info_text.append(f"保留了最近 {preview['kept_recent_count']} 条原始消息 (策略: {preview['compression_strategy']})")
                    if preview["summary"]:
                        info_text.append(f"\n📝 历史摘要: {preview['summary']}", style="italic dim")
                else:
                    info_text.append("\n💤 未启用上下文压缩", style="dim")

                chat_group_items = []
                chat_group_items.append(info_text)
                chat_group_items.append("")
                chat_group_items.append("[bold #61AFEF]💬 最近对话片段预览 (最近 5 条)：[/]")
                
                for message in history[-5:]:
                    role = message.get("role", "?")
                    content = str(message.get("content", ""))
                    
                    content_preview = content[:200] + "..." if len(content) > 200 else content
                    
                    msg_text = Text()
                    if role == "user":
                        msg_text.append("👤 User\n", style="bold #98C379")
                        msg_text.append(content_preview, style="white")
                        border_style = "#98C379"
                    elif role == "assistant":
                        msg_text.append("🤖 Assistant\n", style="bold #56B6C2")
                        msg_text.append(content_preview, style="white")
                        border_style = "#56B6C2"
                    else:
                        msg_text.append(f"⚙️ {role.capitalize()}\n", style="bold #E5C07B")
                        msg_text.append(content_preview, style="dim")
                        border_style = "#3B4252"

                    chat_group_items.append(
                        Panel(
                            msg_text,
                            border_style=border_style,
                            box=box.ROUNDED,
                            padding=(0, 1),
                            expand=False
                        )
                    )

                console.print(
                    Panel(
                        Group(*chat_group_items),
                        title="[bold #61AFEF]🧠 会话记忆快照[/]",
                        border_style="#3B4252",
                        box=box.ROUNDED,
                        padding=(1, 2)
                    )
                )
            except Exception:
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
        else:
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
            if console.__class__.__name__ == "Console":
                render_notification(console, "命令使用方法: /remember <fact>", "warning")
            else:
                console.print("[yellow]Usage: /remember <fact>[/yellow]")
            return True

        entry = store.remember_fact(parts[1].strip())
        if console.__class__.__name__ == "Console":
            render_notification(console, f"已成功记录记忆：{entry['fact']}", "success")
        else:
            console.print(f"[green]Remembered:[/green] {entry['fact']}")
        return True

    if command.startswith("/forget"):
        parts = command.split(maxsplit=1)
        if len(parts) != 2 or not parts[1].strip():
            if console.__class__.__name__ == "Console":
                render_notification(console, "命令使用方法: /forget <keyword>", "warning")
            else:
                console.print("[yellow]Usage: /forget <keyword>[/yellow]")
            return True

        removed = store.forget_fact(parts[1].strip())
        if console.__class__.__name__ == "Console":
            render_notification(console, f"已成功删除 {removed} 条相关记忆。", "success")
        else:
            console.print(f"[green]Forgot {removed} memory item(s).[/green]")
        return True

    if command.startswith("/search"):
        parts = command.split(maxsplit=1)
        if len(parts) != 2 or not parts[1].strip():
            if console.__class__.__name__ == "Console":
                render_notification(console, "命令使用方法: /search <keyword>", "warning")
            else:
                console.print("[yellow]Usage: /search <keyword>[/yellow]")
            return True

        keyword = parts[1].strip()
        results = store.search_memories(keyword)
        semantic = results["semantic"]
        episodic = results["episodic"]
        if not semantic and not episodic:
            if console.__class__.__name__ == "Console":
                render_notification(console, f"未在知识库和对话历史中检索到含有关键字 '{keyword}' 的内容。", "warning")
            else:
                console.print(f"[yellow]No memory matches for:[/yellow] {keyword}")
            return True

        if console.__class__.__name__ == "Console":
            try:
                from rich.panel import Panel
                from rich.console import Group
                from rich.text import Text
                from rich import box
                
                results_list = []
                
                def highlight_match(text: str, kw: str) -> Text:
                    rich_text = Text()
                    pattern = re.compile(re.escape(kw), re.IGNORECASE)
                    last_idx = 0
                    for match in pattern.finditer(text):
                        start, end = match.start(), match.end()
                        rich_text.append(text[last_idx:start], style="default")
                        rich_text.append(text[start:end], style="bold black on #E5C07B")
                        last_idx = end
                    rich_text.append(text[last_idx:], style="default")
                    return rich_text

                if semantic:
                    results_list.append("[bold #E5C07B]🧠 语义记忆匹配结果：[/]")
                    for index, entry in enumerate(semantic, start=1):
                        fact_hl = highlight_match(entry['fact'], keyword)
                        item_text = Text()
                        item_text.append(f"  {index}. ", style="bold #E5C07B")
                        item_text.append(fact_hl)
                        results_list.append(item_text)
                    results_list.append("")

                if episodic:
                    results_list.append("[bold #61AFEF]🎬 情节历史记忆匹配结果：[/]")
                    for index, entry in enumerate(episodic, start=1):
                        summary = entry.get("summary", "")
                        session_id = entry.get("session_id", "unknown-session")
                        summary_hl = highlight_match(summary, keyword)
                        
                        item_text = Text()
                        item_text.append(f"  {index}. ", style="bold #61AFEF")
                        item_text.append(f"[{session_id}] ", style="dim")
                        item_text.append(summary_hl)
                        results_list.append(item_text)

                console.print(
                    Panel(
                        Group(*results_list),
                        title=f"[bold #61AFEF]🔍 记忆检索结果 (关键字: {keyword})[/]",
                        border_style="#3B4252",
                        box=box.ROUNDED,
                        padding=(1, 2)
                    )
                )
            except Exception:
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
        else:
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
            if console.__class__.__name__ == "Console":
                render_notification(console, "长期语义记忆库中目前没有保存任何事实。", "warning")
            else:
                console.print("[yellow]No long-term memories found.[/yellow]")
            return True

        if console.__class__.__name__ == "Console":
            try:
                from rich.panel import Panel
                from rich.text import Text
                from rich import box
                
                content = Text()
                for index, fact in enumerate(facts, start=1):
                    content.append(f"  {index}. ", style="bold #E5C07B")
                    content.append(f"• {fact}\n", style="white")
                content.rstrip()
                
                console.print(
                    Panel(
                        content,
                        title="[bold #E5C07B]🧠 长期语义知识库[/]",
                        border_style="#E5C07B",
                        box=box.ROUNDED,
                        padding=(1, 2)
                    )
                )
            except Exception:
                console.print(
                    f"[yellow]{len(facts)} long-term memories.[/yellow]"
                )
                for index, fact in enumerate(facts, start=1):
                    console.print(f"[cyan]{index}.[/cyan] {fact}")
        else:
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

    if command.startswith("/stream"):
        if runtime is None:
            console.print("[yellow]Streaming control is unavailable in this context.[/yellow]")
            return True

        parts = command.split(maxsplit=1)
        current = bool(runtime.get("stream", True))
        if len(parts) == 1 or parts[1].strip() == "status":
            state = "on" if current else "off"
            console.print(f"[cyan]Streaming:[/cyan] {state}")
            return True

        action = parts[1].strip().lower()
        if action in {"on", "true", "1", "yes"}:
            runtime["stream"] = True
        elif action in {"off", "false", "0", "no"}:
            runtime["stream"] = False
        elif action == "toggle":
            runtime["stream"] = not current
        else:
            console.print("[yellow]Usage: /stream [on|off|toggle|status][/yellow]")
            return True

        state = "on" if runtime["stream"] else "off"
        console.print(f"[green]Streaming {state}.[/green]")
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


def _list_runtime_models(runtime: dict[str, Any]) -> list[str] | None:
    """Fetch model ids from the current OpenAI-compatible client, oldest first."""
    client = runtime.get("client")
    if client is None:
        return None
    try:
        response = client.models.list()
    except Exception:
        return None

    raw_items = response.get("data", []) if isinstance(response, dict) else getattr(response, "data", [])
    model_records: list[tuple[int | None, str]] = []
    for item in raw_items or []:
        if isinstance(item, dict):
            model_id = item.get("id")
            created = item.get("created")
        else:
            model_id = getattr(item, "id", None)
            created = getattr(item, "created", None)
        if model_id:
            model_records.append((_parse_created_timestamp(created), str(model_id)))

    deduped: dict[str, int | None] = {}
    for created, model_id in model_records:
        if model_id not in deduped or _created_is_newer(created, deduped[model_id]):
            deduped[model_id] = created

    return [
        model_id
        for model_id, _created in sorted(
            deduped.items(),
            key=lambda item: (item[1] is not None, item[1] or 0, item[0]),
        )
    ]


def _parse_created_timestamp(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _created_is_newer(candidate: int | None, current: int | None) -> bool:
    if candidate is None:
        return False
    if current is None:
        return True
    return candidate > current


def _switch_runtime_model(runtime: dict[str, Any], model_id: str) -> None:
    """Switch the active model for subsequent requests in the current REPL."""
    loop = runtime.get("loop")
    store = runtime.get("store")
    optimizer = runtime.get("optimizer")
    cfg = runtime.get("config")
    if isinstance(cfg, dict):
        cfg.setdefault("model", {})["model_name"] = model_id

    if loop is not None:
        loop.model = model_id
        loop.token_counter = TokenCounter(model=model_id)
        loop.last_context_window = {}

    if store is not None:
        store.token_counter = TokenCounter(model=model_id)

    if optimizer is not None and hasattr(optimizer, "model"):
        optimizer.model = model_id

    if isinstance(cfg, dict) and store is not None:
        context_limit = resolve_model_context_limit(cfg)
        store.max_context_tokens = context_limit.tokens
        runtime["model_context_limit_source"] = context_limit.source
