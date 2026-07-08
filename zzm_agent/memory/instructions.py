from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class InstructionFile:
    """One project instruction file loaded for the current workspace."""

    path: Path
    name: str
    priority: int
    content: str
    truncated: bool
    original_chars: int
    loaded_chars: int
    version: str


class InstructionManager:
    """Load AGENTS.md / ZZM.md style project instructions with source metadata."""

    def __init__(
        self,
        *,
        workspace_root: str | Path,
        cwd: str | Path | None = None,
        filenames: tuple[str, ...] = ("AGENTS.md", "ZZM.md"),
        max_chars: int = 8000,
    ) -> None:
        self.workspace_root = Path(workspace_root).resolve()
        self.cwd = Path(cwd).resolve() if cwd else self.workspace_root
        self.filenames = tuple(name for name in filenames if name)
        self.max_chars = max(int(max_chars), 0)

    def load(self) -> list[InstructionFile]:
        """Return instruction files from workspace root to nearest directory."""
        if self.max_chars <= 0:
            return []

        directories = self._candidate_directories()
        remaining = self.max_chars
        loaded: list[InstructionFile] = []
        priority = 0

        for directory in directories:
            for filename in self.filenames:
                path = directory / filename
                if not path.is_file():
                    continue

                original = path.read_text(encoding="utf-8", errors="replace")
                original_chars = len(original)
                content = original[:remaining]
                truncated = original_chars > len(content)
                stat = path.stat()
                loaded.append(
                    InstructionFile(
                        path=path,
                        name=filename,
                        priority=priority,
                        content=content,
                        truncated=truncated,
                        original_chars=original_chars,
                        loaded_chars=len(content),
                        version=f"{stat.st_mtime_ns}:{stat.st_size}",
                    )
                )
                priority += 1
                remaining -= len(content)
                if remaining <= 0:
                    return loaded

        return loaded

    def _candidate_directories(self) -> list[Path]:
        """Return root-to-cwd directories without escaping the workspace."""
        root = self.workspace_root
        cwd = self.cwd
        try:
            cwd.relative_to(root)
        except ValueError:
            cwd = root

        parts = [root]
        current = root
        for part in cwd.relative_to(root).parts:
            current = current / part
            parts.append(current)
        return parts
