import os
from pathlib import Path
from zzm_agent.core.tool_registry import tool


def _workspace_root() -> Path:
    root = os.environ.get("ZZM_AGENT_WORKSPACE_ROOT")
    if root:
        return Path(root).expanduser().resolve()
    return Path.cwd().resolve()


def _resolve_workspace_path(path: str) -> Path:
    candidate = Path(path).expanduser().resolve(strict=False)
    workspace_root = _workspace_root()
    if not candidate.is_relative_to(workspace_root):
        raise ValueError(f"Path escapes workspace root: {workspace_root}")
    return candidate


@tool(
    description="读取工作区内指定路径的文件内容，并返回文本（最多 8192 字符）",
    risk_level="low",
)
def read_file(path: str) -> str:
    """
    Read the content of a local file.
    
    Args:
        path: Path to the file, supports '~' expansion.
        
    Returns:
        The content of the file, truncated to 8192 characters.
        If the file is not found, returns an error message.
    """
    try:
        p = _resolve_workspace_path(path)
        
        if not p.exists():
            return f"Error: File not found: {path}"
            
        if not p.is_file():
            return f"Error: Path is not a file: {path}"
            
        # Read with UTF-8 encoding and replace invalid characters
        content = p.read_text(encoding="utf-8", errors="replace")
        
        if not content:
            return "(empty file)"
            
        # Limit content size to prevent context overflow
        return content[:8192]
    except Exception as e:
        return f"Error reading file: {e}"


@tool(
    description="将文本写入工作区内指定文件。如果文件不存在则创建，如果存在则覆盖。",
    risk_level="high",
)
def write_file(path: str, content: str) -> str:
    """
    Write content to a local file.
    
    Args:
        path: Path to the file to create or overwrite.
        content: The text content to write.
        
    Returns:
        A success message indicating how many characters were written.
    """
    try:
        p = _resolve_workspace_path(path)
        
        # Automatically create missing parent directories
        p.parent.mkdir(parents=True, exist_ok=True)
        
        # Write content with UTF-8 encoding
        p.write_text(content, encoding="utf-8")
        
        return f"Success: Written {len(content)} characters to {path}"
    except Exception as e:
        return f"Error writing to file: {e}"
