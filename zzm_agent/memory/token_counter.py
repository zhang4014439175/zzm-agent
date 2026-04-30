from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any


ModelTokenizer = Callable[[str], int]


@dataclass(frozen=True)
class TokenCount:
    """Token count plus the strategy used to produce it."""

    tokens: int
    source: str


class TokenCounter:
    """Count tokens with explicit fallbacks and no hard dependency on tiktoken."""

    def __init__(
        self,
        model: str | None = None,
        model_tokenizers: dict[str, ModelTokenizer] | None = None,
    ) -> None:
        self.model = model or ""
        self.model_tokenizers = model_tokenizers or {}
        self._tiktoken_encoding: Any | None = None
        self._tiktoken_checked = False

    def count(self, text: str) -> TokenCount:
        """Return a token count using model-specific, tiktoken, then len/4 fallback."""
        normalized = str(text or "")
        if not normalized.strip():
            return TokenCount(tokens=0, source="empty")

        model_count = self._count_with_model_tokenizer(normalized)
        if model_count is not None:
            return TokenCount(tokens=model_count, source="model")

        tiktoken_count = self._count_with_tiktoken(normalized)
        if tiktoken_count is not None:
            return TokenCount(tokens=tiktoken_count, source="tiktoken")

        return TokenCount(tokens=max(1, (len(" ".join(normalized.split())) + 3) // 4), source="len/4")

    def count_text(self, text: str) -> int:
        """Return only the numeric token count."""
        return self.count(text).tokens

    def _count_with_model_tokenizer(self, text: str) -> int | None:
        if not self.model:
            return None

        tokenizer = self.model_tokenizers.get(self.model)
        if tokenizer is None:
            for prefix, candidate in self.model_tokenizers.items():
                if prefix and self.model.startswith(prefix):
                    tokenizer = candidate
                    break
        if tokenizer is None:
            return None

        try:
            count = int(tokenizer(text))
        except Exception:
            return None
        return max(count, 0)

    def _count_with_tiktoken(self, text: str) -> int | None:
        encoding = self._get_tiktoken_encoding()
        if encoding is None:
            return None
        try:
            return len(encoding.encode(text))
        except Exception:
            return None

    def _get_tiktoken_encoding(self) -> Any | None:
        if self._tiktoken_checked:
            return self._tiktoken_encoding

        self._tiktoken_checked = True
        try:
            import tiktoken  # type: ignore
        except ImportError:
            self._tiktoken_encoding = None
            return None

        try:
            if self.model:
                self._tiktoken_encoding = tiktoken.encoding_for_model(self.model)
            else:
                self._tiktoken_encoding = tiktoken.get_encoding("cl100k_base")
        except Exception:
            try:
                self._tiktoken_encoding = tiktoken.get_encoding("cl100k_base")
            except Exception:
                self._tiktoken_encoding = None
        return self._tiktoken_encoding
