from __future__ import annotations

import re
from dataclasses import dataclass

from .token_budget import TokenEstimator


@dataclass(frozen=True)
class TextChunk:
    index: int
    text: str
    estimated_tokens: int


class TokenAwareChunker:
    SENTENCE_BOUNDARY = re.compile(r"(?<=[。！？!?；;\n])")

    def __init__(self, estimator: TokenEstimator | None = None) -> None:
        self.estimator = estimator or TokenEstimator()

    def split(self, text: str, chunk_size: int = 450, overlap: int = 80) -> list[TextChunk]:
        if not text or not text.strip():
            raise ValueError("text must not be blank")
        if chunk_size <= 0:
            raise ValueError("chunk_size must be positive")
        if overlap < 0 or overlap >= chunk_size:
            raise ValueError("overlap must be >= 0 and < chunk_size")

        units = [part.strip() for part in self.SENTENCE_BOUNDARY.split(text) if part.strip()]
        if not units:
            units = [text.strip()]

        chunks: list[str] = []
        current: list[str] = []
        current_tokens = 0

        for unit in units:
            for segment in self._split_oversized(unit, chunk_size):
                tokens = self.estimator.count(segment)
                if current and current_tokens + tokens > chunk_size:
                    chunk_text = "".join(current).strip()
                    chunks.append(chunk_text)
                    overlap_text = self._tail_by_tokens(chunk_text, overlap)
                    current = [overlap_text] if overlap_text else []
                    current_tokens = self.estimator.count(overlap_text)
                current.append(segment)
                current_tokens += tokens

        if current:
            final_text = "".join(current).strip()
            if final_text and (not chunks or final_text != chunks[-1]):
                chunks.append(final_text)

        return [
            TextChunk(index=index, text=value, estimated_tokens=self.estimator.count(value))
            for index, value in enumerate(chunks)
        ]

    def _split_oversized(self, text: str, chunk_size: int) -> list[str]:
        if self.estimator.count(text) <= chunk_size:
            return [text]
        approximate_chars = max(120, chunk_size * 2)
        return [text[i : i + approximate_chars] for i in range(0, len(text), approximate_chars)]

    def _tail_by_tokens(self, text: str, token_limit: int) -> str:
        if token_limit <= 0:
            return ""
        low = 0
        high = len(text)
        while low < high:
            mid = (low + high) // 2
            candidate = text[mid:]
            if self.estimator.count(candidate) > token_limit:
                low = mid + 1
            else:
                high = mid
        return text[low:].strip()
