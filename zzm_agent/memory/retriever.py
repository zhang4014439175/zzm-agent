from __future__ import annotations

import re
from abc import ABC, abstractmethod


class MemoryRetriever(ABC):
    """Abstract interface for long-term memory retrieval strategies."""

    @abstractmethod
    def search(
        self,
        query: str,
        semantic_entries: list[dict],
        episodic_entries: list[dict],
        limit: int,
    ) -> dict[str, list[dict]]:
        """Return the most relevant semantic and episodic memories."""


class KeywordMemoryRetriever(MemoryRetriever):
    """Retrieve memories by keyword overlap and substring matching."""

    _WORD_PATTERN = re.compile(r"[A-Za-z0-9_]+", re.UNICODE)

    def search(
        self,
        query: str,
        semantic_entries: list[dict],
        episodic_entries: list[dict],
        limit: int,
    ) -> dict[str, list[dict]]:
        if limit <= 0:
            return {"semantic": [], "episodic": []}

        normalized_query = self._normalize(query)
        if not normalized_query:
            return {"semantic": [], "episodic": []}

        terms = self._extract_terms(normalized_query)
        semantic = self._rank_entries(
            entries=semantic_entries,
            text_key="fact",
            terms=terms,
            normalized_query=normalized_query,
            limit=limit,
        )
        episodic = self._rank_entries(
            entries=episodic_entries,
            text_key="summary",
            terms=terms,
            normalized_query=normalized_query,
            limit=limit,
        )
        return {"semantic": semantic, "episodic": episodic}

    def _rank_entries(
        self,
        entries: list[dict],
        text_key: str,
        terms: list[str],
        normalized_query: str,
        limit: int,
    ) -> list[dict]:
        ranked: list[tuple[int, str, dict]] = []
        for entry in entries:
            text = self._normalize(entry.get(text_key, ""))
            score = self._score_text(text, terms, normalized_query)
            if score <= 0:
                continue
            ranked.append((score, str(entry.get("updated_at", "")), entry))

        ranked.sort(key=lambda item: (item[0], item[1]), reverse=True)
        return [entry for _score, _updated_at, entry in ranked[:limit]]

    def _score_text(
        self,
        text: str,
        terms: list[str],
        normalized_query: str,
    ) -> int:
        if not text:
            return 0

        score = 0
        if normalized_query in text:
            score += 5

        for term in terms:
            if term in text:
                score += 1

        return score

    def _extract_terms(self, normalized_query: str) -> list[str]:
        terms = []
        if len(normalized_query) >= 2:
            terms.append(normalized_query)

        for token in self._WORD_PATTERN.findall(normalized_query):
            token = token.strip().lower()
            if len(token) >= 2:
                terms.append(token)

        unique_terms: list[str] = []
        seen = set()
        for term in terms:
            if term in seen:
                continue
            seen.add(term)
            unique_terms.append(term)
        return unique_terms

    def _normalize(self, text: object) -> str:
        return " ".join(str(text).lower().split())
