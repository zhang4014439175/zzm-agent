import os
from pathlib import Path

from zzm_agent.core.tool_registry import tool


def _workspace_root() -> Path:
    root = os.environ.get("ZZM_AGENT_WORKSPACE_ROOT")
    if root:
        return Path(root).expanduser().resolve()
    return Path.cwd().resolve()


def _resolve_workspace_path(path: str) -> Path:
    """Resolve a user-provided path and ensure it stays inside the workspace."""
    workspace_root = _workspace_root()
    expanded = Path(path).expanduser()
    # Treat relative paths as relative to the workspace root, not the real CWD.
    if not expanded.is_absolute():
        candidate = (workspace_root / expanded).resolve(strict=False)
    else:
        candidate = expanded.resolve(strict=False)
    # Resolve symlinks to prevent sandbox escapes via symbolic links
    try:
        real_candidate = candidate.resolve(strict=True) if candidate.exists() else candidate
    except OSError:
        real_candidate = candidate
    if not real_candidate.is_relative_to(workspace_root):
        raise ValueError(f"Path escapes workspace root: {workspace_root}")
    return candidate


@tool(
    description=(
        "读取工作区内指定路径的文件内容。支持指定起止行号（1-indexed）进行分页读取。"
        "返回带行号的文本内容，便于后续精确编辑。"
    ),
    risk_level="low",
)
def read_file(path: str, start_line: int = 0, end_line: int = 0) -> str:
    """
    Read the content of a local file with line numbers.

    Args:
        path: Path to the file, supports '~' expansion.
        start_line: Starting line number (1-indexed, inclusive). 0 means from beginning.
        end_line: Ending line number (1-indexed, inclusive). 0 means to end.

    Returns:
        The content of the file with line numbers, truncated if too large.
    """
    try:
        p = _resolve_workspace_path(path)

        if not p.exists():
            return f"Error: File not found: {path}"

        if not p.is_file():
            return f"Error: Path is not a file: {path}"

        content = p.read_text(encoding="utf-8", errors="replace")
        if not content:
            return "(empty file)"

        lines = content.splitlines(keepends=True)
        total_lines = len(lines)

        # Apply line range if specified
        s = max(1, start_line) if start_line > 0 else 1
        e = min(total_lines, end_line) if end_line > 0 else total_lines

        if s > total_lines:
            return f"Error: start_line {s} exceeds total lines {total_lines}"
        if s > e:
            return f"Error: start_line {s} > end_line {e}"

        selected = lines[s - 1 : e]

        # Build output with line numbers
        numbered_lines = []
        for i, line in enumerate(selected, start=s):
            # Strip the trailing newline for clean display, re-add it
            numbered_lines.append(f"{i:>6}: {line.rstrip()}")

        header = f"File: {path} | Lines {s}-{e} of {total_lines}\n"
        result = header + "\n".join(numbered_lines)

        # Limit output size to prevent context overflow
        max_chars = 12000
        if len(result) > max_chars:
            result = result[:max_chars] + f"\n... (truncated, showing {max_chars} of {len(result)} chars)"

        return result
    except Exception as e:
        return f"Error reading file: {e}"


@tool(
    description=(
        "在文件中精确查找目标文本并替换为新内容。支持单次或多次替换。"
        "这是修改文件的首选方式——不需要重写整个文件，只替换需要改的部分。"
    ),
    risk_level="medium",
)
def file_edit(path: str, target: str, replacement: str, replace_all: str = "false") -> str:
    """
    Perform a precise search-and-replace edit within a file.

    Args:
        path: Path to the file to edit.
        target: The exact text to find in the file (must match exactly, including whitespace).
        replacement: The text to replace the target with.
        replace_all: If "true", replace all occurrences. Default "false" replaces only the first.

    Returns:
        A success message with the number of replacements made, or an error.
    """
    try:
        p = _resolve_workspace_path(path)

        if not p.exists():
            return f"Error: File not found: {path}"
        if not p.is_file():
            return f"Error: Path is not a file: {path}"

        content = p.read_text(encoding="utf-8", errors="replace")

        if target not in content:
            # Provide context to help the model correct its target text
            preview = content[:500]
            return (
                f"Error: Target text not found in {path}.\n"
                f"File begins with:\n{preview}\n"
                "Hint: Use read_file first to see exact content with line numbers."
            )

        do_all = replace_all.strip().lower() in {"true", "yes", "1"}

        if do_all:
            count = content.count(target)
            new_content = content.replace(target, replacement)
        else:
            count = 1
            new_content = content.replace(target, replacement, 1)

        p.write_text(new_content, encoding="utf-8")

        # Show a brief diff preview
        target_preview = target[:80].replace("\n", "\\n")
        replacement_preview = replacement[:80].replace("\n", "\\n")
        return (
            f"Success: Replaced {count} occurrence(s) in {path}\n"
            f"  - '{target_preview}'\n"
            f"  + '{replacement_preview}'"
        )
    except Exception as e:
        return f"Error editing file: {e}"


@tool(
    description="将文本写入工作区内指定文件。如果文件不存在则创建，如果存在则覆盖。",
    risk_level="high",
)
def write_file(path: str, content: str) -> str:
    """
    Write content to a local file (full overwrite).

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


@tool(
    description="向工作区内指定文件追加内容。如果文件不存在则创建。",
    risk_level="medium",
)
def file_append(path: str, content: str) -> str:
    """
    Append content to the end of a file.

    Args:
        path: Path to the file to append to.
        content: The text content to append.

    Returns:
        A success message.
    """
    try:
        p = _resolve_workspace_path(path)
        p.parent.mkdir(parents=True, exist_ok=True)

        with p.open("a", encoding="utf-8") as f:
            f.write(content)

        return f"Success: Appended {len(content)} characters to {path}"
    except Exception as e:
        return f"Error appending to file: {e}"


@tool(
    description=(
        "列出工作区内指定目录的内容，包括文件名、类型、大小。"
        "支持递归列出子目录（默认仅列出一层）。"
    ),
    risk_level="low",
)
def list_directory(path: str = ".", recursive: str = "false", max_depth: int = 1) -> str:
    """
    List contents of a directory within the workspace.

    Args:
        path: Path to the directory. Defaults to workspace root ".".
        recursive: If "true", list recursively. Default "false".
        max_depth: Maximum depth for recursive listing (1-5). Default 1.

    Returns:
        A formatted directory listing with file types and sizes.
    """
    try:
        p = _resolve_workspace_path(path)

        if not p.exists():
            return f"Error: Directory not found: {path}"
        if not p.is_dir():
            return f"Error: Path is not a directory: {path}"

        do_recursive = recursive.strip().lower() in {"true", "yes", "1"}
        depth = max(1, min(5, max_depth))

        entries = []
        _collect_entries(p, p, entries, do_recursive, depth, current_depth=0)

        if not entries:
            return f"(empty directory: {path})"

        # Sort: directories first, then files, alphabetically
        entries.sort(key=lambda e: (e["type"] != "DIR", e["rel_path"].lower()))

        lines = [f"Directory: {path} ({len(entries)} items)\n"]
        lines.append(f"{'Type':<5} {'Size':>10}  {'Path'}")
        lines.append("-" * 60)

        for entry in entries:
            type_str = entry["type"]
            size_str = _human_size(entry["size"]) if entry["type"] == "FILE" else ""
            lines.append(f"{type_str:<5} {size_str:>10}  {entry['rel_path']}")

        result = "\n".join(lines)

        # Limit output
        if len(result) > 12000:
            result = result[:12000] + f"\n... (truncated, {len(entries)} total items)"

        return result
    except Exception as e:
        return f"Error listing directory: {e}"


def _collect_entries(
    base: Path,
    current: Path,
    entries: list,
    recursive: bool,
    max_depth: int,
    current_depth: int,
) -> None:
    """Recursively collect directory entries up to the specified depth."""
    if current_depth > max_depth:
        return

    try:
        children = sorted(current.iterdir(), key=lambda x: x.name.lower())
    except PermissionError:
        return

    for child in children:
        # Skip hidden files/dirs and common non-essential directories
        if child.name.startswith(".") or child.name in {
            "__pycache__",
            "node_modules",
            ".git",
            ".venv",
            "venv",
            ".tox",
        }:
            continue

        rel_path = child.relative_to(base)
        if child.is_dir():
            entries.append({
                "type": "DIR",
                "rel_path": str(rel_path) + "/",
                "size": 0,
            })
            if recursive and current_depth < max_depth:
                _collect_entries(base, child, entries, recursive, max_depth, current_depth + 1)
        elif child.is_file():
            try:
                size = child.stat().st_size
            except OSError:
                size = 0
            entries.append({
                "type": "FILE",
                "rel_path": str(rel_path),
                "size": size,
            })


@tool(
    description="获取文件的详细元信息：大小、行数、修改时间、编码检测等。",
    risk_level="low",
)
def file_info(path: str) -> str:
    """
    Get detailed metadata about a file.

    Args:
        path: Path to the file.

    Returns:
        File metadata including size, line count, modification time, etc.
    """
    try:
        p = _resolve_workspace_path(path)

        if not p.exists():
            return f"Error: File not found: {path}"

        stat = p.stat()
        info_lines = [f"File: {path}"]
        info_lines.append(f"  Size: {_human_size(stat.st_size)} ({stat.st_size} bytes)")

        from datetime import datetime, timezone

        mtime = datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc)
        info_lines.append(f"  Modified: {mtime.isoformat()}")

        if p.is_file():
            try:
                content = p.read_text(encoding="utf-8", errors="replace")
                line_count = content.count("\n") + (1 if content and not content.endswith("\n") else 0)
                info_lines.append(f"  Lines: {line_count}")
                info_lines.append(f"  Characters: {len(content)}")
                info_lines.append(f"  Extension: {p.suffix or '(none)'}")
            except Exception:
                info_lines.append("  (unable to read file content)")
        elif p.is_dir():
            info_lines.append("  Type: Directory")
            try:
                children = list(p.iterdir())
                info_lines.append(f"  Children: {len(children)}")
            except PermissionError:
                info_lines.append("  Children: (permission denied)")

        return "\n".join(info_lines)
    except Exception as e:
        return f"Error getting file info: {e}"


def _human_size(size: int) -> str:
    """Format a byte count as a human-readable string."""
    for unit in ("B", "KB", "MB", "GB"):
        if abs(size) < 1024:
            return f"{size:,.0f} {unit}" if unit == "B" else f"{size:,.1f} {unit}"
        size /= 1024
    return f"{size:,.1f} TB"
