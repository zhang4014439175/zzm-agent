from __future__ import annotations

import argparse
import json
import os
from getpass import getpass
from pathlib import Path
from typing import Any

from zzm_agent.constants import TOOL_EVENTS_PATH, ZZM_AGENT_DIR
from zzm_agent.cli_support.observability import CliObserver
from zzm_agent.cli_support.repl import _start_working_status, _stop_working_status
from zzm_agent.cli_support.rendering import build_console
from zzm_agent.core.agent_loop import AgentLoop
from zzm_agent.core.change_set import ChangeSetStore
from zzm_agent.core.config import ConfigManager
from zzm_agent.core.model_metadata import resolve_model_context_limit
from zzm_agent.core.observability import ToolEvent, ToolEventCallback, ToolEventLogger
from zzm_agent.core.provider_headers import build_provider_default_headers
from zzm_agent.core.query_engine import QueryEngine
from zzm_agent.core.tool_registry import ToolRegistry, set_active_registry
from zzm_agent.core.mcp_client import MCPError, StdioMCPClient
from zzm_agent.evolution.optimizer import EvolutionOptimizer
from zzm_agent.memory.io import StorageCorruptionError
from zzm_agent.memory.store import MemoryStore
from zzm_agent.prompt.manager import PromptManager
from zzm_agent.skills import SkillManager

CONFIG_PATH = Path("config.yaml")
DEFAULT_BASE_URL = "https://api.openai.com/v1"
DEFAULT_MODEL_NAME = "gpt-4o-mini"

class FirstRunSetupRequired(RuntimeError):
    """Raised when first-run setup needs interactive input."""

class MissingModelConfig(RuntimeError):
    """Raised when model credentials are missing after config loading."""

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

    exec_parser = subparsers.add_parser("exec", help="Run one non-interactive agent task")
    exec_parser.add_argument("prompt", nargs="*", help="Task prompt. Multiple words are joined with spaces.")
    exec_parser.add_argument("--stdin", action="store_true", help="Append stdin content to the task prompt.")
    exec_parser.add_argument("--json", action="store_true", dest="json_output", help="Emit JSONL stream events to stdout.")
    exec_parser.add_argument("--output", "-o", dest="output_path", help="Write the final assistant message to a file.")
    exec_parser.add_argument("--session", dest="session_id", help="Resume or create a specific session id.")
    exec_parser.add_argument("--config", dest="config_path", help="Path to the YAML config file.")
    exec_parser.add_argument("--safe", action="store_true", help="Use stricter confirmation policies.")
    exec_parser.add_argument("--debug", action="store_true", help="Show full tracebacks for runtime errors.")

    completion_parser = subparsers.add_parser("completion", help="Print shell completion script")
    completion_parser.add_argument(
        "shell",
        nargs="?",
        choices=["bash", "zsh", "powershell"],
        default="bash",
        help="Shell type to generate completion for.",
    )

    if argv is None:
        argv = sys.argv[1:]
    if not argv or argv[0] not in ["repl", "eval", "exec", "completion"]:
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


def _default_user_config_dir() -> Path:
    return Path.home() / ".zzm_agent"


def _default_user_config_path() -> Path:
    return _default_user_config_dir() / "config.yaml"


def _default_user_env_path() -> Path:
    return _default_user_config_dir() / ".env"


def _installed_plugin_dir() -> Path:
    return Path(__file__).resolve().parents[1] / "plugins"


def _default_config_text() -> str:
    """生成首次初始化使用的完整 YAML 配置模板。

    模板中的工具轮次是单 Segment 上限，超长工具结果阈值决定何时 Artifact 化，
    自动续段次数则是跨 Segment 的资源保险丝；这些默认值必须与运行时解析策略
    保持一致，避免新旧配置表现不同。
    """
    plugin_dir = str(_installed_plugin_dir()).replace("\\", "/")
    return (
        "model:\n"
        f'  base_url: "${{LLM_BASE_URL:-{DEFAULT_BASE_URL}}}"\n'
        '  api_key: "${LLM_API_KEY}"\n'
        f'  model_name: "${{LLM_MODEL_NAME:-{DEFAULT_MODEL_NAME}}}"\n'
        '  openrouter_referer: "${OPENROUTER_APP_URL:-https://github.com/zhang4014439175/zzm-agent}"\n'
        '  openrouter_title: "${OPENROUTER_APP_NAME:-zzm-agent}"\n'
        "  temperature: 0.7\n"
        "  max_tokens: 4096\n"
        "  context_window_tokens:\n"
        "  input_price_per_1m: 0\n"
        "  output_price_per_1m: 0\n"
        "\n"
        "agent:\n"
        '  system_prompt: "You are zzm-agent, a concise and helpful personal assistant."\n'
        "  auto_approve: false\n"
        "  max_tool_iterations: 20\n"
        "  duplicate_tool_call_limit: 3\n"
        "  max_tool_retries: 1\n"
        "  empty_final_retries: 2\n"
        "  max_inline_tool_result_tokens: 2000\n"
        "  max_auto_continuations: 8\n"
        "  stream: true\n"
        '  tool_choice: "auto"\n'
        "  plugin_dirs:\n"
        f'    - "{plugin_dir}"\n'
        "\n"
        "ui:\n"
        '  response_language: "auto"\n'
        '  default_locale_language: "zh-CN"\n'
        "\n"
        "memory:\n"
        '  path: "~/.zzm_agent/memory.json"\n'
        "  max_history: 50\n"
        "  retrieval_top_k: 3\n"
        "  max_context_tokens: 32000\n"
        "  compression_keep_recent: 10\n"
        "  instruction_files:\n"
        '    - "AGENTS.md"\n'
        '    - "ZZM.md"\n'
        "  instruction_max_chars: 8000\n"
        "  auto_memory_enabled: true\n"
        "\n"
        "skills:\n"
        '  directories: [".zzm_agent/skills", "~/.zzm_agent/skills"]\n'
        "  disabled: []\n"
        "  pinned: []\n"
        "  max_skill_tokens: 2000\n"
        "  max_resource_tokens: 1000\n"
        "\n"
        "evolution:\n"
        "  enabled: false\n"
        '  trigger: "manual"\n'
        "  sample_size: 20\n"
        "  history_versions: 5\n"
        "  auto_interval:\n"
        "  threshold:\n"
    )


def _dotenv_escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')


def _write_dotenv_values(env_path: Path, values: dict[str, str]) -> None:
    existing: dict[str, str] = {}
    ordered_keys: list[str] = []
    if env_path.exists():
        for line in env_path.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = key.strip()
            if key:
                existing[key] = value.strip().strip('"')
                ordered_keys.append(key)

    for key, value in values.items():
        if value:
            existing[key] = value
            if key not in ordered_keys:
                ordered_keys.append(key)

    env_path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "# zzm-agent local model credentials.",
        "# This file is intentionally stored outside project repositories by default.",
    ]
    for key in ordered_keys:
        if key in existing:
            lines.append(f'{key}="{_dotenv_escape(existing[key])}"')
    env_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _load_dotenv_file(path: Path) -> None:
    if not path.exists():
        return
    try:
        from dotenv import load_dotenv
    except ImportError:
        for line in path.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#") or "=" not in stripped:
                continue
            key, value = stripped.split("=", 1)
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            os.environ.setdefault(key, value)
        return
    load_dotenv(path, override=False)


def _load_dotenv_files(sources: list[Any]) -> None:
    _load_dotenv_file(Path.cwd() / ".env")
    seen: set[Path] = set()
    for source in sources:
        path = (Path(source.path).parent / ".env").resolve()
        if path not in seen:
            seen.add(path)
            _load_dotenv_file(path)


def create_first_run_config(
    *,
    config_path: Path | None = None,
    env_path: Path | None = None,
    base_url: str = DEFAULT_BASE_URL,
    model_name: str = DEFAULT_MODEL_NAME,
    api_key: str = "",
) -> Path:
    """Create a user-level config and optional .env values for first run."""
    target_config = config_path or _default_user_config_path()
    target_env = env_path or target_config.parent / ".env"
    target_config.parent.mkdir(parents=True, exist_ok=True)
    if not target_config.exists():
        target_config.write_text(_default_config_text(), encoding="utf-8")
    _write_dotenv_values(
        target_env,
        {
            "LLM_BASE_URL": base_url,
            "LLM_MODEL_NAME": model_name,
            "LLM_API_KEY": api_key,
        },
    )
    return target_config


def prompt_for_model_config(
    *,
    input_func: Any = input,
    secret_input_func: Any = getpass,
    output_func: Any = print,
    base_url: str = DEFAULT_BASE_URL,
    model_name: str = DEFAULT_MODEL_NAME,
) -> dict[str, str]:
    """Ask for model settings and return values suitable for .env."""
    output_func("zzm-agent first-run setup")
    output_func("Enter your OpenAI-compatible model settings. Press Enter to keep defaults.")
    raw_base_url = input_func(f"Base URL [{base_url}]: ").strip()
    raw_model_name = input_func(f"Model name [{model_name}]: ").strip()
    api_key = secret_input_func("LLM API key: ").strip()
    return {
        "base_url": raw_base_url or base_url,
        "model_name": raw_model_name or model_name,
        "api_key": api_key,
    }


def ensure_first_run_config(args: argparse.Namespace, *, stdin: Any = None) -> Path | None:
    """Create a user config on first interactive REPL startup."""
    import sys

    stdin = stdin or sys.stdin
    if getattr(args, "command", "repl") != "repl":
        return None
    if getattr(args, "config_path", None) or os.environ.get("ZZM_AGENT_CONFIG"):
        return None

    manager = ConfigManager()
    if manager.resolve_default_sources(None):
        return None
    if not getattr(stdin, "isatty", lambda: False)():
        raise FirstRunSetupRequired(
            "No config found. Run zzm-agent in an interactive terminal once, "
            "or create ~/.zzm_agent/config.yaml, or pass --config."
        )

    values = prompt_for_model_config()
    if not values["api_key"]:
        raise MissingModelConfig(
            "LLM API key is required. Re-run zzm-agent and enter a key, "
            "or set LLM_API_KEY / ZZM_AGENT_API_KEY / OPENAI_API_KEY."
        )
    config_path = create_first_run_config(**values)
    os.environ.setdefault("LLM_BASE_URL", values["base_url"])
    os.environ.setdefault("LLM_MODEL_NAME", values["model_name"])
    os.environ.setdefault("LLM_API_KEY", values["api_key"])
    print(f"Created config: {config_path}")
    print(f"Saved credentials: {config_path.parent / '.env'}")
    return config_path


def ensure_model_credentials(cfg: dict[str, Any], args: argparse.Namespace, *, stdin: Any = None) -> None:
    """Prompt for missing model credentials and persist them beside the config."""
    import sys

    model_cfg = cfg.setdefault("model", {})
    api_key = (
        model_cfg.get("api_key")
        or os.environ.get("LLM_API_KEY")
        or os.environ.get("ZZM_AGENT_API_KEY")
        or os.environ.get("OPENAI_API_KEY")
    )
    if api_key:
        return
    if getattr(args, "command", "repl") != "repl":
        raise MissingModelConfig(
            "Model API key is required. Set model.api_key, LLM_API_KEY, "
            "ZZM_AGENT_API_KEY, or OPENAI_API_KEY."
        )
    stdin = stdin or sys.stdin
    if not getattr(stdin, "isatty", lambda: False)():
        raise MissingModelConfig(
            "Model API key is required. Run zzm-agent interactively once, "
            "or set LLM_API_KEY / ZZM_AGENT_API_KEY / OPENAI_API_KEY."
        )

    base_url = str(model_cfg.get("base_url") or DEFAULT_BASE_URL)
    model_name = str(model_cfg.get("model_name") or DEFAULT_MODEL_NAME)
    values = prompt_for_model_config(base_url=base_url, model_name=model_name)
    if not values["api_key"]:
        raise MissingModelConfig("LLM API key is required before starting zzm-agent.")

    config_dir = Path(cfg.get("_config_dir") or _default_user_config_dir()).expanduser()
    _write_dotenv_values(
        config_dir / ".env",
        {
            "LLM_BASE_URL": values["base_url"],
            "LLM_MODEL_NAME": values["model_name"],
            "LLM_API_KEY": values["api_key"],
        },
    )
    os.environ.setdefault("LLM_BASE_URL", values["base_url"])
    os.environ.setdefault("LLM_MODEL_NAME", values["model_name"])
    os.environ.setdefault("LLM_API_KEY", values["api_key"])
    model_cfg["base_url"] = model_cfg.get("base_url") or values["base_url"]
    model_cfg["model_name"] = model_cfg.get("model_name") or values["model_name"]
    model_cfg["api_key"] = values["api_key"]


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
        import yaml
    except ImportError as exc:
        raise RuntimeError("PyYAML is required to load config.yaml.") from exc

    _ = yaml
    manager = ConfigManager()
    _load_dotenv_file(Path.cwd() / ".env")
    sources = manager.resolve_default_sources(config_path)
    if not sources:
        raise FirstRunSetupRequired(
            "config.yaml not found. Run zzm-agent in an interactive terminal once, "
            "or use --config, or set ZZM_AGENT_CONFIG."
        )
    _load_dotenv_files(sources)
    return manager.load(sources=sources).config


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


def build_noninteractive_confirmation_callback(console: Any):
    """Return a confirmation callback that never blocks for user input."""

    def confirm_tool(name: str, arguments: dict[str, Any], risk_level: str) -> bool:
        console.print(
            f"[yellow]Denied {risk_level} risk tool in non-interactive exec mode:[/yellow] "
            f"{name} {_format_compact_arguments(arguments)}"
        )
        return False

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
    _load_mcp_servers(registry, cfg)

    return registry


def _load_mcp_servers(registry: ToolRegistry, cfg: dict[str, Any]) -> None:
    """加载配置中的最小 stdio MCP 服务，并把失败隔离为诊断信息。

    每项只接受服务名、命令数组和可选超时。连接成功后工具由注册表统一管理；
    单个服务启动或协议失败不会阻止本地插件与其他服务继续可用，错误留在
    ``registry.mcp_errors`` 供命令层展示。HTTP 等传输不在本阶段解析。
    """
    mcp_cfg = cfg.get("mcp", {})
    servers = mcp_cfg.get("servers", []) if isinstance(mcp_cfg, dict) else []
    for raw in servers if isinstance(servers, list) else []:
        if not isinstance(raw, dict) or raw.get("enabled", True) is False:
            continue
        name = raw.get("name")
        command = raw.get("command")
        if not isinstance(name, str) or not isinstance(command, list) or not all(isinstance(x, str) for x in command):
            registry.mcp_errors.append("Invalid MCP server configuration")
            continue
        try:
            timeout_seconds = float(raw.get("timeout_seconds", 15))
            if timeout_seconds <= 0:
                raise ValueError("timeout_seconds must be positive")
            client = StdioMCPClient(name, command, timeout_seconds=timeout_seconds)
            client.connect(registry)
        except (MCPError, TypeError, ValueError) as exc:
            registry.mcp_errors.append(f"MCP server {name}: {exc}")
        else:
            registry.mcp_clients.append(client)


def get_agent_loop_policy(cfg: dict[str, Any]) -> dict[str, int]:
    """读取并规范化 AgentLoop 的分段执行与恢复策略。

    对旧配置补齐稳定默认值，并对各字段设置最小合法范围。这里的最大工具轮次
    不代表终止整个用户任务，而是触发 yielded 检查点；超长结果阈值至少为 1，
    防止零值导致所有工具输出被意外 Artifact 化。
    """
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
        "empty_final_retries": max(
            0,
            int(agent_cfg.get("empty_final_retries", 2)),
        ),
        "max_inline_tool_result_tokens": max(
            1,
            int(agent_cfg.get("max_inline_tool_result_tokens", 2000)),
        ),
    }


def build_runtime(args: argparse.Namespace, cfg: dict[str, Any]) -> dict[str, Any]:
    """装配 CLI 执行所需的全部运行时对象。

    该方法解析 Provider、Workspace、MemoryStore、工具目录、Hook、ChangeSet、
    AgentLoop 和 QueryEngine，并把配置中的分段策略注入正确层级。AgentLoop 只
    处理单段，QueryEngine 负责自动续段与快照；最终返回的依赖字典同时供交互式
    REPL 和非交互 exec 使用。缺少 SDK 或 API Key 时会在任何模型调用前明确失败。
    """
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
        default_headers=build_provider_default_headers(cfg["model"]),
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
    change_sets = ChangeSetStore(workspace_root, session_id=store.session_id)

    def capture_change_start(event: ToolEvent) -> None:
        # Session switching happens in the CLI without rebuilding the registry.
        # Bind each recorded write to whichever session is active at execution.
        change_sets.session_id = store.session_id
        change_sets.capture_start(event)
    prompt_manager = PromptManager(
        base_prompt=system_prompt,
        workspace_root=workspace_root,
        registry=registry,
    )
    skills_cfg = cfg.get("skills", {})
    config_dir = Path(cfg.get("_config_dir") or workspace_root)
    skill_directories: list[Path] = []
    for raw_path in skills_cfg.get(
        "directories", [".zzm_agent/skills", "~/.zzm_agent/skills"]
    ):
        skill_path = Path(str(raw_path)).expanduser()
        if not skill_path.is_absolute():
            skill_path = config_dir / skill_path
        skill_directories.append(skill_path.resolve())
    skill_manager = SkillManager(
        skill_directories,
        disabled={str(item) for item in skills_cfg.get("disabled", [])},
        pinned={str(item) for item in skills_cfg.get("pinned", [])},
        max_skill_tokens=skills_cfg.get("max_skill_tokens", 2000),
        max_resource_tokens=skills_cfg.get("max_resource_tokens", 1000),
        token_counter=store.estimate_text_tokens,
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
        confirm_tool=(
            build_noninteractive_confirmation_callback(console)
            if getattr(args, "command", "repl") == "exec"
            else build_tool_confirmation_callback(console)
        ),
        max_tool_iterations=loop_policy["max_tool_iterations"],
        duplicate_tool_call_limit=loop_policy["duplicate_tool_call_limit"],
        max_tool_retries=loop_policy["max_tool_retries"],
        empty_final_retries=loop_policy["empty_final_retries"],
        max_inline_tool_result_tokens=loop_policy[
            "max_inline_tool_result_tokens"
        ],
        tool_choice=cfg.get("agent", {}).get("tool_choice", "auto"),
        on_tool_start=_fanout_tool_callbacks(
            observer.on_tool_start, tool_event_logger, capture_change_start
        ),
        on_tool_end=_fanout_tool_callbacks(
            observer.on_tool_end,
            tool_event_logger,
            lambda event: change_sets.capture_end(
                event,
                turn_id=(loop.last_turn_state.turn_id if loop.last_turn_state else None),
            ),
        ),
        on_tool_error=_fanout_tool_callbacks(
            observer.on_tool_error, tool_event_logger, change_sets.capture_end
        ),
        prompt_manager=prompt_manager,
        skill_manager=skill_manager,
    )
    snapshot_path = workspace_root / ZZM_AGENT_DIR / "state" / f"{store.session_id}.json"
    query_engine = QueryEngine.with_snapshot_path(
        agent_loop=loop,
        snapshot_path=snapshot_path,
        config=cfg,
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
        "skills": skill_manager,
        "observer": observer,
        "change_sets": change_sets,
        "model_context_limit_source": context_limit.source,
        "stream": _config_bool(cfg.get("agent", {}).get("stream"), default=True),
        "debug": bool(getattr(args, "debug", False)),
    }


def render_completion_script(shell: str) -> str:
    """Return a lightweight static completion script for common shells."""
    commands = "repl eval exec completion"
    options = "--help --config --session --safe --debug --stdin --json --output --suite --llm"
    if shell == "powershell":
        return (
            "Register-ArgumentCompleter -Native -CommandName zzm-agent -ScriptBlock {\n"
            "  param($wordToComplete, $commandAst, $cursorPosition)\n"
            f"  '{commands} {options}'.Split(' ') | Where-Object {{ $_ -like \"$wordToComplete*\" }}\n"
            "}\n"
        )
    if shell == "zsh":
        return (
            "#compdef zzm-agent\n"
            "_arguments '*::arg:->args'\n"
            "case $state in\n"
            f"  args) compadd {commands} {options} ;;\n"
            "esac\n"
        )
    return (
        "_zzm_agent_complete() {\n"
        f"  COMPREPLY=( $(compgen -W \"{commands} {options}\" -- \"${{COMP_WORDS[COMP_CWORD]}}\") )\n"
        "}\n"
        "complete -F _zzm_agent_complete zzm-agent\n"
    )


def main(argv: list[str] | None = None) -> int:
    import sys
    from zzm_agent.cli_support.execution import run_exec
    from zzm_agent.cli_support.repl import run_repl
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
    runtime: dict[str, Any] | None = None
    try:
        args = parse_args(argv)
        if getattr(args, "command", "repl") == "completion":
            print(render_completion_script(args.shell), end="")
            return 0

        ensure_first_run_config(args)
        cfg = load_config(args.config_path)
        
        if getattr(args, "command", "repl") == "eval":
            from zzm_agent.eval.runner import run_eval
            return run_eval(args.suite, args.llm, cfg)

        ensure_model_credentials(cfg, args)
        runtime = build_runtime(args, cfg)
        if getattr(args, "command", "repl") == "exec":
            stdin_text = sys.stdin.read() if getattr(args, "stdin", False) else ""
            return run_exec(runtime, args, stdin_text=stdin_text)
        return run_repl(runtime)
    except StorageCorruptionError as exc:
        console = build_console()
        if args is not None and getattr(args, "debug", False):
            console.print_exception()
        else:
            console.print(f"[red]Storage corruption: {exc}[/red]")
        return 1
    except (FirstRunSetupRequired, MissingModelConfig) as exc:
        console = build_console()
        console.print(f"[yellow]{exc}[/yellow]")
        return 2
    except Exception:
        console = build_console()
        if args is not None and getattr(args, "debug", False):
            console.print_exception()
        else:
            console.print("[red]Unexpected error occurred. Re-run with --debug for traceback.[/red]")
        return 1
    finally:
        if runtime is not None:
            registry = runtime.get("registry")
            if registry is not None:
                registry.shutdown_mcp_clients()
