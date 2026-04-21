import os
import shlex
import subprocess
import sys
from pathlib import Path

from zzm_agent.core.tool_registry import tool


def _workspace_root() -> Path:
    root = os.environ.get("ZZM_AGENT_WORKSPACE_ROOT")
    if root:
        return Path(root).expanduser().resolve()
    return Path.cwd().resolve()


@tool(
    description=(
        "在工作区内执行命令并返回 stdout、stderr 和退出码。"
        "支持自定义超时时间（默认 30 秒，最大 300 秒）和工作目录。"
    ),
    risk_level="high",
)
def run_shell(command: str, timeout: int = 30, cwd: str = "") -> str:
    """
    Execute a shell command locally and capture its output.

    Args:
        command: The shell command to execute.
        timeout: Maximum execution time in seconds (1-300). Default 30.
        cwd: Working directory for the command. Defaults to workspace root.

    Returns:
        The combined stdout, stderr, and exit code, truncated if too large.
    """
    try:
        # On Windows, shlex.split may not handle paths correctly
        if sys.platform == "win32":
            argv = command
            use_shell = True
        else:
            argv = shlex.split(command)
            if not argv:
                return "Error: Command cannot be empty."
            use_shell = False

        # Resolve working directory
        work_dir = _workspace_root()
        if cwd:
            candidate = Path(cwd).expanduser().resolve(strict=False)
            if candidate.is_relative_to(work_dir) and candidate.is_dir():
                work_dir = candidate

        # Clamp timeout to [1, 300] seconds
        timeout_sec = max(1, min(300, timeout))

        result = subprocess.run(
            argv,
            shell=use_shell,
            capture_output=True,
            text=False,
            timeout=timeout_sec,
            cwd=work_dir,
        )

        # Decode stdout and stderr with 'replace' to avoid surrogate characters
        stdout = (result.stdout or b"").decode("utf-8", errors="replace")
        stderr = (result.stderr or b"").decode("utf-8", errors="replace")

        parts = []
        if stdout.strip():
            parts.append(f"[stdout]\n{stdout.rstrip()}")
        if stderr.strip():
            parts.append(f"[stderr]\n{stderr.rstrip()}")

        exit_info = f"[exit code: {result.returncode}]"

        if not parts:
            return f"(no output) {exit_info}"

        output = "\n\n".join(parts)

        # Truncate to avoid overwhelming the model context
        max_chars = 8192
        if len(output) > max_chars:
            output = output[:max_chars] + f"\n... (truncated at {max_chars} chars)"

        return f"{output}\n\n{exit_info}"
    except subprocess.TimeoutExpired:
        return f"Error: Command timed out after {timeout_sec} seconds."
    except Exception as e:
        return f"Error executing command: {e}"


@tool(
    description=(
        "获取当前操作系统和工作区环境信息：系统类型、Python版本、工作区路径、"
        "已安装的工具（git/node/go 等）。"
    ),
    risk_level="low",
)
def environment_info() -> str:
    """
    Get information about the current operating environment.

    Returns:
        System info, Python version, workspace path, and available tool versions.
    """
    import platform

    lines = [
        f"OS: {platform.system()} {platform.release()} ({platform.machine()})",
        f"Python: {platform.python_version()}",
        f"Workspace: {_workspace_root()}",
    ]

    # Check for common development tools
    tools_to_check = ["git", "node", "npm", "go", "rustc", "java", "gcc", "make"]
    available = []
    for tool_name in tools_to_check:
        try:
            result = subprocess.run(
                [tool_name, "--version"],
                capture_output=True,
                text=True,
                timeout=5,
                cwd=_workspace_root(),
            )
            if result.returncode == 0:
                version = result.stdout.strip().splitlines()[0][:80]
                available.append(f"  {tool_name}: {version}")
        except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
            continue

    if available:
        lines.append("Available tools:")
        lines.extend(available)
    else:
        lines.append("Available tools: (none detected)")

    return "\n".join(lines)
