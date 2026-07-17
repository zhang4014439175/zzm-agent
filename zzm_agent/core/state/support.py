from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()

def _content_hash(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8", errors="replace")).hexdigest()


def _stable_id(prefix: str, payload: Any) -> str:
    serialized = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)
    digest = hashlib.sha256(serialized.encode("utf-8")).hexdigest()[:16]
    return f"{prefix}-{digest}"


@dataclass
class FileReadRange:
    """One range read from a cached file."""

    start_line: int
    end_line: int
    read_at: str = field(default_factory=_utc_now_iso)

    def to_record(self) -> dict[str, Any]:
        return {
            "start_line": self.start_line,
            "end_line": self.end_line,
            "read_at": self.read_at,
        }

    @classmethod
    def from_record(cls, record: dict[str, Any]) -> "FileReadRange":
        return cls(
            start_line=int(record.get("start_line", 1)),
            end_line=int(record.get("end_line", 1)),
            read_at=str(record.get("read_at") or _utc_now_iso()),
        )


@dataclass
class FileState:
    """Cached metadata and optional content for one workspace file."""

    normalized_path: str
    content_hash: str
    size_bytes: int
    mtime_ns: int
    encoding: str = "utf-8"
    line_count: int = 0
    content: str | None = None
    content_reference: str | None = None
    read_ranges: list[FileReadRange] = field(default_factory=list)
    summary: str = ""
    last_read_at: str | None = None
    agent_last_modified_at: str | None = None
    version: int = 1

    def matches_file(self, *, size_bytes: int, mtime_ns: int) -> bool:
        return self.size_bytes == size_bytes and self.mtime_ns == mtime_ns

    def has_range(self, start_line: int, end_line: int) -> bool:
        return any(
            item.start_line <= start_line and item.end_line >= end_line
            for item in self.read_ranges
        )

    def record_range(self, start_line: int, end_line: int) -> None:
        self.read_ranges.append(FileReadRange(start_line=start_line, end_line=end_line))
        self.last_read_at = self.read_ranges[-1].read_at

    def to_record(self) -> dict[str, Any]:
        return {
            "normalized_path": self.normalized_path,
            "content_hash": self.content_hash,
            "size_bytes": self.size_bytes,
            "mtime_ns": self.mtime_ns,
            "encoding": self.encoding,
            "line_count": self.line_count,
            "content": self.content,
            "content_reference": self.content_reference,
            "read_ranges": [item.to_record() for item in self.read_ranges],
            "summary": self.summary,
            "last_read_at": self.last_read_at,
            "agent_last_modified_at": self.agent_last_modified_at,
            "version": self.version,
        }

    @classmethod
    def from_record(cls, record: dict[str, Any]) -> "FileState":
        return cls(
            normalized_path=str(record["normalized_path"]),
            content_hash=str(record.get("content_hash", "")),
            size_bytes=int(record.get("size_bytes", 0)),
            mtime_ns=int(record.get("mtime_ns", 0)),
            encoding=str(record.get("encoding", "utf-8")),
            line_count=int(record.get("line_count", 0)),
            content=record.get("content"),
            content_reference=record.get("content_reference"),
            read_ranges=[
                FileReadRange.from_record(item)
                for item in record.get("read_ranges", [])
                if isinstance(item, dict)
            ],
            summary=str(record.get("summary", "")),
            last_read_at=record.get("last_read_at"),
            agent_last_modified_at=record.get("agent_last_modified_at"),
            version=int(record.get("version", 1)),
        )


@dataclass
class FileStateCache:
    """Runtime cache for file reads and agent file writes."""

    files: dict[str, FileState] = field(default_factory=dict)
    invalidated_paths: set[str] = field(default_factory=set)

    def get_valid(
        self,
        *,
        normalized_path: str,
        size_bytes: int,
        mtime_ns: int,
    ) -> FileState | None:
        state = self.files.get(normalized_path)
        if state is None:
            return None
        if state.matches_file(size_bytes=size_bytes, mtime_ns=mtime_ns):
            return state
        self.invalidate(normalized_path)
        return None

    def record_read(
        self,
        *,
        normalized_path: str,
        content: str,
        size_bytes: int,
        mtime_ns: int,
        start_line: int,
        end_line: int,
        encoding: str = "utf-8",
    ) -> FileState:
        previous = self.files.get(normalized_path)
        version = previous.version + 1 if previous else 1
        lines = content.splitlines()
        summary = self._summarize_content(content)
        state = FileState(
            normalized_path=normalized_path,
            content_hash=_content_hash(content),
            size_bytes=size_bytes,
            mtime_ns=mtime_ns,
            encoding=encoding,
            line_count=len(lines),
            content=content,
            read_ranges=[],
            summary=summary,
            version=version,
        )
        state.record_range(start_line=start_line, end_line=end_line)
        self.files[normalized_path] = state
        self.invalidated_paths.discard(normalized_path)
        return state

    def update_after_write(
        self,
        *,
        normalized_path: str,
        content: str,
        size_bytes: int,
        mtime_ns: int,
        encoding: str = "utf-8",
    ) -> FileState:
        state = self.record_read(
            normalized_path=normalized_path,
            content=content,
            size_bytes=size_bytes,
            mtime_ns=mtime_ns,
            start_line=1,
            end_line=max(1, len(content.splitlines())),
            encoding=encoding,
        )
        state.agent_last_modified_at = _utc_now_iso()
        return state

    def invalidate(self, normalized_path: str) -> None:
        self.files.pop(normalized_path, None)
        self.invalidated_paths.add(normalized_path)

    def to_record(self) -> dict[str, Any]:
        return {
            "files": {
                path: state.to_record()
                for path, state in self.files.items()
            },
            "invalidated_paths": sorted(self.invalidated_paths),
        }

    @classmethod
    def from_record(cls, record: dict[str, Any] | None) -> "FileStateCache":
        if not record:
            return cls()
        return cls(
            files={
                str(path): FileState.from_record(state)
                for path, state in record.get("files", {}).items()
                if isinstance(state, dict)
            },
            invalidated_paths=set(record.get("invalidated_paths", [])),
        )

    def _summarize_content(self, content: str, limit: int = 240) -> str:
        text = " ".join(content.split())
        if len(text) <= limit:
            return text
        return text[: limit - 3] + "..."


@dataclass
class MemorySourceRecord:
    """One memory source injected or loaded into a model turn."""

    source_id: str
    source_type: str
    content: str
    path: str | None = None
    version: str | None = None
    loaded_at: str = field(default_factory=_utc_now_iso)

    def to_record(self) -> dict[str, Any]:
        return {
            "source_id": self.source_id,
            "source_type": self.source_type,
            "content": self.content,
            "path": self.path,
            "version": self.version,
            "loaded_at": self.loaded_at,
        }

    @classmethod
    def from_record(cls, record: dict[str, Any]) -> "MemorySourceRecord":
        return cls(
            source_id=str(record["source_id"]),
            source_type=str(record["source_type"]),
            content=str(record.get("content", "")),
            path=record.get("path"),
            version=record.get("version"),
            loaded_at=str(record.get("loaded_at") or _utc_now_iso()),
        )


@dataclass
class MemoryLoadState:
    """Track memory sources loaded for one model turn."""

    loaded_project_memory_paths: dict[str, str] = field(default_factory=dict)
    loaded_nested_memory_paths: dict[str, str] = field(default_factory=dict)
    loaded_skill_reference_paths: dict[str, str] = field(default_factory=dict)
    injected_semantic_memory_ids: set[str] = field(default_factory=set)
    injected_episodic_memory_ids: set[str] = field(default_factory=set)
    memory_file_versions: dict[str, str] = field(default_factory=dict)
    sources: list[MemorySourceRecord] = field(default_factory=list)
    duplicate_sources: list[str] = field(default_factory=list)

    def record_file_source(
        self,
        *,
        path: str,
        source_type: str,
        version: str,
        content: str = "",
    ) -> bool:
        target = self._path_bucket(source_type)
        normalized_path = str(path)
        if normalized_path in target and target[normalized_path] == version:
            self.duplicate_sources.append(normalized_path)
            return False
        target[normalized_path] = version
        self.memory_file_versions[normalized_path] = version
        self.sources.append(
            MemorySourceRecord(
                source_id=_stable_id(source_type, {"path": normalized_path, "version": version}),
                source_type=source_type,
                content=content,
                path=normalized_path,
                version=version,
            )
        )
        return True

    def record_semantic_memory(self, entry: dict[str, Any]) -> bool:
        source_id = self.semantic_memory_id(entry)
        if source_id in self.injected_semantic_memory_ids:
            self.duplicate_sources.append(source_id)
            return False
        self.injected_semantic_memory_ids.add(source_id)
        self.sources.append(
            MemorySourceRecord(
                source_id=source_id,
                source_type="semantic",
                content=str(entry.get("fact", "")),
                version=str(entry.get("updated_at", "")),
            )
        )
        return True

    def record_episodic_memory(self, entry: dict[str, Any]) -> bool:
        source_id = self.episodic_memory_id(entry)
        if source_id in self.injected_episodic_memory_ids:
            self.duplicate_sources.append(source_id)
            return False
        self.injected_episodic_memory_ids.add(source_id)
        self.sources.append(
            MemorySourceRecord(
                source_id=source_id,
                source_type="episodic",
                content=str(entry.get("summary", "")),
                path=entry.get("session_id"),
                version=str(entry.get("updated_at", "")),
            )
        )
        return True

    def semantic_memory_id(self, entry: dict[str, Any]) -> str:
        return _stable_id(
            "semantic",
            {
                "fact": entry.get("normalized_fact") or entry.get("fact", ""),
                "updated_at": entry.get("updated_at", ""),
            },
        )

    def episodic_memory_id(self, entry: dict[str, Any]) -> str:
        return _stable_id(
            "episodic",
            {
                "session_id": entry.get("session_id", ""),
                "summary": entry.get("summary", ""),
                "updated_at": entry.get("updated_at", ""),
            },
        )

    def to_record(self) -> dict[str, Any]:
        return {
            "loaded_project_memory_paths": dict(self.loaded_project_memory_paths),
            "loaded_nested_memory_paths": dict(self.loaded_nested_memory_paths),
            "loaded_skill_reference_paths": dict(self.loaded_skill_reference_paths),
            "injected_semantic_memory_ids": sorted(self.injected_semantic_memory_ids),
            "injected_episodic_memory_ids": sorted(self.injected_episodic_memory_ids),
            "memory_file_versions": dict(self.memory_file_versions),
            "sources": [source.to_record() for source in self.sources],
            "duplicate_sources": list(self.duplicate_sources),
        }

    @classmethod
    def from_record(cls, record: dict[str, Any] | None) -> "MemoryLoadState":
        if not record:
            return cls()
        return cls(
            loaded_project_memory_paths=dict(record.get("loaded_project_memory_paths", {})),
            loaded_nested_memory_paths=dict(record.get("loaded_nested_memory_paths", {})),
            loaded_skill_reference_paths=dict(record.get("loaded_skill_reference_paths", {})),
            injected_semantic_memory_ids=set(record.get("injected_semantic_memory_ids", [])),
            injected_episodic_memory_ids=set(record.get("injected_episodic_memory_ids", [])),
            memory_file_versions=dict(record.get("memory_file_versions", {})),
            sources=[
                MemorySourceRecord.from_record(source)
                for source in record.get("sources", [])
                if isinstance(source, dict)
            ],
            duplicate_sources=list(record.get("duplicate_sources", [])),
        )

    def _path_bucket(self, source_type: str) -> dict[str, str]:
        if source_type == "nested_memory":
            return self.loaded_nested_memory_paths
        if source_type == "skill_reference":
            return self.loaded_skill_reference_paths
        return self.loaded_project_memory_paths
