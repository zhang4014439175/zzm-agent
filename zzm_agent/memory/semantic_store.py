from __future__ import annotations

from pathlib import Path

from zzm_agent.memory.io import StorageIO


class SemanticStore:
    """Persist cross-session semantic memory facts."""

    def __init__(self, io: StorageIO, base_dir: Path):
        self.io = io
        self.semantic_path = base_dir / "semantic.json" q

    def load(self) -> list[dict]:
        """Load cross-session semantic memory entries ordered by recency."""
        data = self.io.read_json(self.semantic_path, default=[])
        if not isinstance(data, list):
            return []
        entries = [
            entry for entry in data if isinstance(entry, dict) and entry.get("fact")
        ]
        return sorted(
            entries,
            key=lambda entry: entry.get("updated_at", ""),
            reverse=True,
        )

    def list_facts(self) -> list[str]:
        """Return every semantic memory fact ordered by recency."""
        return [entry["fact"] for entry in self.load() if entry.get("fact")]

    def remember(self, fact: str, now: str) -> dict:
        """Insert or refresh one semantic memory fact."""
        cleaned = fact.strip()
        if not cleaned:
            raise ValueError("Fact cannot be empty.")

        normalized = self._normalize_text(cleaned)
        entries = self.load()
        for entry in entries:
            # Re-remembering the same fact refreshes it instead of duplicating
            # semantic memory injections across future sessions.
            if entry.get("normalized_fact") == normalized:
                entry["fact"] = cleaned
                entry["updated_at"] = now
                self.io.write_json(self.semantic_path, entries)
                return entry

        entry = {
            "fact": cleaned,
            "normalized_fact": normalized,
            "created_at": now,
            "updated_at": now,
        }
        entries.append(entry)
        self.io.write_json(self.semantic_path, entries)
        return entry

    def forget(self, keyword: str) -> int:
        """Remove semantic memory entries whose text matches the keyword."""
        normalized = self._normalize_text(keyword)
        if not normalized:
            raise ValueError("Keyword cannot be empty.")

        existing = self.load()
        retained = [
            entry
            for entry in existing
            if normalized not in self._normalize_text(entry.get("fact", ""))
        ]
        removed = len(existing) - len(retained)
        if removed:
            self.io.write_json(self.semantic_path, retained)
        return removed

    def _normalize_text(self, text: str) -> str:
        """Normalize memory text for de-duplication and keyword matching."""
        return " ".join(text.lower().split())
