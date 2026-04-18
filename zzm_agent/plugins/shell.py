import os
import shlex
import subprocess
from pathlib import Path

from zzm_agent.core.tool_registry import tool


def _workspace_root() -> Path:
    root = os.environ.get("ZZM_AGENT_WORKSPACE_ROOT")
    if root:
        return Path(root).expanduser().resolve()
    return Path.cwd().resolve()


@tool(
    description="在工作区内执行单条命令并返回 stdout 和 stderr 的合并输出（最多 4096 字符）",
    risk_level="high",
)
def run_shell(command: str) -> str:
    """
    Execute a shell command locally and capture its output.
    
    Args:
        command: The shell command to execute.
        
    Returns:
        The combined stdout and stderr of the command, truncated to 4096 characters.
        If no output is generated, returns '(no output)'.
    """
    try:
        argv = shlex.split(command)
        if not argv:
            return "Error: Command cannot be empty."

        # Execute one program directly to avoid shell expansion/injection.
        result = subprocess.run(
            argv,
            shell=False,
            capture_output=True,
            text=False, # Get raw bytes to handle encoding manually
            timeout=30,
            cwd=_workspace_root(),
        )
        
        # Decode stdout and stderr with 'replace' to avoid surrogate characters
        stdout = (result.stdout or b"").decode("utf-8", errors="replace")
        stderr = (result.stderr or b"").decode("utf-8", errors="replace")
        output = stdout + stderr
        
        if not output:
            return "(no output)"
            
        # Truncate to avoid overwhelming the model context
        return output[:4096]
    except subprocess.TimeoutExpired:
        return "Error: Command timed out after 30 seconds."
    except Exception as e:
        return f"Error executing command: {e}"
