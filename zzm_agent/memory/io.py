from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path


class StorageCorruptionError(RuntimeError):
    """Raised when a persisted JSON file is corrupt and had to be quarantined."""

    def __init__(
        self,
        path: Path,
        backup_path: Path | None = None,
        quarantine_path: Path | None = None,
        restored_from_backup: bool = False,
    ):
        self.path = path
        self.backup_path = backup_path
        self.quarantine_path = quarantine_path
        self.restored_from_backup = restored_from_backup

        details = [f"Corrupt JSON detected at {path}."]
        if quarantine_path is not None:
            details.append(f"Moved corrupt file to {quarantine_path}.")
        if restored_from_backup and backup_path is not None:
            details.append(f"Restored the last good backup from {backup_path}.")
        else:
            details.append("Initialized a fresh replacement file; inspect the quarantined copy.")
        super().__init__(" ".join(details))


class StorageIO:
    """Provide shared filesystem helpers for memory persistence."""

    def read_json(self, path: Path, default):
        """Read one JSON file and surface corruption instead of hiding it."""
        if not path.exists():
            return default
        try:
            with path.open("r", encoding="utf-8", errors="replace") as handle:
                return json.load(handle)
        except json.JSONDecodeError as exc:
            raise self._recover_from_corrupt_json(path, default) from exc
        except OSError:
            raise

    def write_json(self, path: Path, data) -> None:
        """Write one JSON file atomically enough for local persistence."""
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = path.with_suffix(path.suffix + ".tmp")
        
        # Generate JSON string first
        json_str = json.dumps(data, ensure_ascii=False, indent=2)
        # Encode to bytes manually with 'replace' to avoid surrogate character errors
        json_bytes = json_str.encode("utf-8", errors="replace")
        
        with tmp_path.open("wb") as handle:
            handle.write(json_bytes)
        tmp_path.replace(path)
        self._write_backup(path)

    def write_text(self, path: Path, value: str) -> None:
        """Write one text file via a temporary file and replace."""
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = path.with_suffix(path.suffix + ".tmp")
        
        # Encode to bytes manually with 'replace' to avoid surrogate character errors
        text_bytes = value.encode("utf-8", errors="replace")
        
        with tmp_path.open("wb") as handle:
            handle.write(text_bytes)
        tmp_path.replace(path)
        self._write_backup(path)

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
            self._write_backup(path)
            return

        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass
        try:
            self.backup_path(path).unlink(missing_ok=True)
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

    def backup_path(self, path: Path) -> Path:
        """Return the sidecar backup path for one file."""
        return path.with_suffix(path.suffix + ".bak")

    def quarantine_path(self, path: Path) -> Path:
        """Return a timestamped quarantine path for one corrupt file."""
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        return path.with_suffix(path.suffix + f".corrupt.{stamp}")

    def _write_backup(self, path: Path) -> None:
        """Refresh the sidecar backup with the current file bytes."""
        backup_path = self.backup_path(path)
        backup_path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = backup_path.with_suffix(backup_path.suffix + ".tmp")
        with path.open("rb") as source, tmp_path.open("wb") as handle:
            handle.write(source.read())
        tmp_path.replace(backup_path)

    def _recover_from_corrupt_json(self, path: Path, default) -> StorageCorruptionError:
        """Quarantine corrupt JSON and restore the most recent good state when possible."""
        quarantine_path = self.quarantine_path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.replace(quarantine_path)

        backup_path = self.backup_path(path)
        restored_from_backup = False
        if backup_path.exists():
            backup_bytes = self.read_bytes(backup_path)
            if backup_bytes is not None:
                try:
                    json.loads(backup_bytes.decode("utf-8"))
                except (UnicodeDecodeError, json.JSONDecodeError):
                    backup_bytes = None
            if backup_bytes is not None:
                self.restore_file(path, backup_bytes, existed=True)
                restored_from_backup = True

        if not restored_from_backup:
            if default is None or isinstance(default, (dict, list)):
                self.write_json(path, default)
            elif isinstance(default, str):
                self.write_text(path, default)
            else:
                self.write_text(path, str(default))

        return StorageCorruptionError(
            path=path,
            backup_path=backup_path if backup_path.exists() else None,
            quarantine_path=quarantine_path,
            restored_from_backup=restored_from_backup,
        )
