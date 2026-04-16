from __future__ import annotations

import json
from pathlib import Path


class StorageIO:
    """Provide shared filesystem helpers for memory persistence."""

    def read_json(self, path: Path, default):
        """Read one JSON file and fall back to a default value on failure."""
        if not path.exists():
            return default
        try:
            with path.open("r", encoding="utf-8") as handle:
                return json.load(handle)
        except (json.JSONDecodeError, OSError):
            return default

    def write_json(self, path: Path, data) -> None:
        """Write one JSON file atomically enough for local persistence."""
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = path.with_suffix(path.suffix + ".tmp")
        with tmp_path.open("w", encoding="utf-8") as handle:
            json.dump(data, handle, ensure_ascii=False, indent=2)
        tmp_path.replace(path)

    def write_text(self, path: Path, value: str) -> None:
        """Write one text file via a temporary file and replace."""
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = path.with_suffix(path.suffix + ".tmp")
        with tmp_path.open("w", encoding="utf-8") as handle:
            handle.write(value)
        tmp_path.replace(path)

    def read_bytes(self, path: Path) -> bytes | None:
        """Read raw bytes from one file when available."""
        if not path.exists():
            return None
        try:
            return path.read_bytes()
        except OSError:
            return None

    def restore_file(self, path: Path, content: bytes | None, existed: bool) -> None:
        """Restore one file to its previous bytes or remove it if absent before."""
        if existed and content is not None:
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp_path = path.with_suffix(path.suffix + ".tmp")
            with tmp_path.open("wb") as handle:
                handle.write(content)
            tmp_path.replace(path)
            return

        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass

    def remove_tree(self, path: Path) -> None:
        """Remove one directory tree recursively."""
        if not path.exists():
            return
        for child in path.iterdir():
            if child.is_dir():
                self.remove_tree(child)
            else:
                child.unlink(missing_ok=True)
        path.rmdir()
