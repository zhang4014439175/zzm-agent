import subprocess
from zzm_agent.core.tool_registry import tool


@tool(description="在本机执行 shell 命令，并返回 stdout 和 stderr 的合并输出（最多 4096 字符）")
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
        # Execute the command with a 30-second timeout
        result = subprocess.run(
            command,
            shell=True,
            capture_output=True,
            text=True,
            timeout=30,
        )
        
        # Combine stdout and stderr
        output = (result.stdout or "") + (result.stderr or "")
        
        if not output:
            return "(no output)"
            
        # Truncate to avoid overwhelming the model context
        return output[:4096]
    except subprocess.TimeoutExpired:
        return "Error: Command timed out after 30 seconds."
    except Exception as e:
        return f"Error executing command: {e}"
