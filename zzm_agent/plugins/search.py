import os
import re
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
    if not candidate.is_relative_to(workspace_root):
        raise ValueError(f"Path escapes workspace root: {workspace_root}")
    return candidate


# Binary file extensions to skip during text search
_BINARY_EXTENSIONS = frozenset({
    ".png", ".jpg", ".jpeg", ".gif", ".bmp", ".ico", ".webp", ".svg",
    ".mp3", ".mp4", ".avi", ".mov", ".wav", ".flac",
    ".zip", ".tar", ".gz", ".bz2", ".7z", ".rar",
    ".pdf", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx",
    ".exe", ".dll", ".so", ".dylib", ".bin", ".dat",
    ".pyc", ".pyo", ".class", ".o", ".obj",
    ".woff", ".woff2", ".ttf", ".eot",
    ".sqlite", ".db",
})

# Directories to always skip during search
_SKIP_DIRS = frozenset({
    ".git", ".svn", ".hg",
    "__pycache__", ".mypy_cache", ".pytest_cache", ".ruff_cache",
    "node_modules", ".tox", ".venv", "venv", "env",
    ".idea", ".vscode", ".vs",
    "dist", "build", ".eggs", "*.egg-info",
})


def _should_skip_dir(name: str) -> bool:
    """Check if a directory name should be excluded from search."""
    return name in _SKIP_DIRS or name.startswith(".")


def _is_binary(path: Path) -> bool:
    """Heuristic check if a file is binary."""
    return path.suffix.lower() in _BINARY_EXTENSIONS


@tool(
    description=(
        "在工作区内搜索文件内容，支持正则表达式和精确匹配。"
        "可指定文件扩展名过滤（如 '*.py'）、是否区分大小写。"
        "返回匹配行及其行号和文件路径。"
    ),
    risk_level="low",
)
def grep_search(
    pattern: str,
    path: str = ".",
    include: str = "",
    case_sensitive: str = "true",
    is_regex: str = "false",
    max_results: int = 50,
) -> str:
    """
    Search for a pattern across files in the workspace (like grep/ripgrep).

    Args:
        pattern: The text or regex pattern to search for.
        path: Directory or file to search in. Defaults to workspace root.
        include: Glob pattern to filter files, e.g. "*.py" or "*.go". Empty means all files.
        case_sensitive: "true" for case-sensitive, "false" for case-insensitive. Default "true".
        is_regex: "true" to treat pattern as regex, "false" for literal. Default "false".
        max_results: Maximum number of matching lines to return. Default 50.

    Returns:
        Matching lines with file paths and line numbers.
    """
    try:
        p = _resolve_workspace_path(path)
        workspace = _workspace_root()

        if not p.exists():
            return f"Error: Path not found: {path}"

        case = case_sensitive.strip().lower() not in {"false", "no", "0"}
        regex_mode = is_regex.strip().lower() in {"true", "yes", "1"}
        max_res = max(1, min(200, max_results))

        # Compile the search pattern
        flags = 0 if case else re.IGNORECASE
        try:
            if regex_mode:
                compiled = re.compile(pattern, flags)
            else:
                compiled = re.compile(re.escape(pattern), flags)
        except re.error as e:
            return f"Error: Invalid regex pattern: {e}"

        # Parse include glob
        include_suffix = ""
        if include.strip():
            # Support patterns like "*.py", "*.go,*.js"
            include_suffix = include.strip()

        # Collect files to search
        files = _collect_search_files(p, include_suffix)

        matches = []
        files_with_matches = set()

        for file_path in files:
            if len(matches) >= max_res:
                break

            try:
                content = file_path.read_text(encoding="utf-8", errors="replace")
            except (OSError, PermissionError):
                continue

            for line_num, line in enumerate(content.splitlines(), start=1):
                if len(matches) >= max_res:
                    break
                if compiled.search(line):
                    try:
                        rel = file_path.relative_to(workspace)
                    except ValueError:
                        rel = file_path
                    files_with_matches.add(str(rel))
                    # Trim very long lines
                    display_line = line.rstrip()
                    if len(display_line) > 200:
                        display_line = display_line[:200] + "..."
                    matches.append(f"{rel}:{line_num}: {display_line}")

        if not matches:
            scope = f" in {path}" if path != "." else ""
            filter_hint = f" (filter: {include})" if include else ""
            return f"No matches found for '{pattern}'{scope}{filter_hint}"

        header = (
            f"Found {len(matches)} match(es) in {len(files_with_matches)} file(s)"
        )
        if len(matches) >= max_res:
            header += f" (capped at {max_res})"

        result = header + "\n\n" + "\n".join(matches)
        if len(result) > 12000:
            result = result[:12000] + "\n... (output truncated)"
        return result
    except Exception as e:
        return f"Error searching: {e}"


def _collect_search_files(root: Path, include_glob: str) -> list[Path]:
    """Collect files to search, respecting exclusion rules and include globs."""
    files: list[Path] = []

    if root.is_file():
        if not _is_binary(root):
            files.append(root)
        return files

    # Parse multiple include globs separated by commas
    globs = []
    if include_glob:
        globs = [g.strip() for g in include_glob.split(",") if g.strip()]

    if globs:
        # Use glob matching for each pattern
        for glob_pattern in globs:
            for match in root.rglob(glob_pattern):
                if match.is_file() and not _is_binary(match):
                    # Check if any parent dir should be skipped
                    if not any(_should_skip_dir(part) for part in match.relative_to(root).parts[:-1]):
                        files.append(match)
    else:
        # Walk all files
        for match in sorted(root.rglob("*")):
            if not match.is_file():
                continue
            if _is_binary(match):
                continue
            try:
                rel_parts = match.relative_to(root).parts
            except ValueError:
                continue
            if any(_should_skip_dir(part) for part in rel_parts[:-1]):
                continue
            files.append(match)

    # Sort for deterministic output and limit total files scanned
    files.sort()
    return files[:5000]


@tool(
    description=(
        "在工作区内按文件名查找文件。支持通配符模式（如 '*.py'、'test_*'）。"
        "返回匹配的文件路径和大小。"
    ),
    risk_level="low",
)
def find_files(name_pattern: str, path: str = ".", max_results: int = 50) -> str:
    """
    Find files by name pattern within the workspace.

    Args:
        name_pattern: Glob pattern for file names, e.g. "*.py", "test_*", "README*".
        path: Directory to search in. Defaults to workspace root.
        max_results: Maximum number of results to return. Default 50.

    Returns:
        A list of matching file paths with sizes.
    """
    try:
        p = _resolve_workspace_path(path)
        workspace = _workspace_root()

        if not p.exists():
            return f"Error: Directory not found: {path}"
        if not p.is_dir():
            return f"Error: Path is not a directory: {path}"

        max_res = max(1, min(500, max_results))
        results: list[tuple[str, int, bool]] = []

        for match in p.rglob(name_pattern):
            try:
                rel_parts = match.relative_to(p).parts
            except ValueError:
                continue
            # Skip excluded directories
            if any(_should_skip_dir(part) for part in rel_parts[:-1]):
                continue

            try:
                rel = match.relative_to(workspace)
            except ValueError:
                rel = match

            is_dir = match.is_dir()
            size = match.stat().st_size if match.is_file() else 0
            results.append((str(rel), size, is_dir))

            if len(results) >= max_res:
                break

        if not results:
            return f"No files found matching '{name_pattern}' in {path}"

        results.sort(key=lambda r: r[0].lower())

        lines = [f"Found {len(results)} result(s) matching '{name_pattern}':\n"]
        for rel_path, size, is_dir in results:
            if is_dir:
                lines.append(f"  DIR   {rel_path}/")
            else:
                lines.append(f"  FILE  {_human_size(size):>10}  {rel_path}")

        return "\n".join(lines)
    except Exception as e:
        return f"Error finding files: {e}"


def _human_size(size: int) -> str:
    """Format a byte count as a human-readable string."""
    for unit in ("B", "KB", "MB", "GB"):
        if abs(size) < 1024:
            return f"{size:,.0f} {unit}" if unit == "B" else f"{size:,.1f} {unit}"
        size /= 1024
    return f"{size:,.1f} TB"
