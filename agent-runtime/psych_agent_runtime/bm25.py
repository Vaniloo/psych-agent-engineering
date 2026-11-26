from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass

_latin = re.compile(r"[a-z0-9_]+")
_cjk_sequence = re.compile(r"[\u3400-\u9fff]+")


def tokenize_for_search(text: str) -> list[str]:
    normalized = text.lower()
    tokens = _latin.findall(normalized)
    for sequence in _cjk_sequence.findall(normalized):
        # Character bigrams, no unigrams: single characters such as "的" or "是"
        # would otherwise give almost every document a keyword "hit".
        if len(sequence) == 1:
            tokens.append(sequence)
        tokens.extend(sequence[i : i + 2] for i in range(len(sequence) - 1))
    return [token for token in tokens if token.strip()]


@dataclass(frozen=True)
class ScoredDocument:
    index: int
    score: float


class BM25:
    def __init__(self, documents: list[str], k1: float = 1.5, b: float = 0.75) -> None:
        self.k1 = k1
        self.b = b
        self.tokenized = [tokenize_for_search(document) for document in documents]
        self.lengths = [len(tokens) for tokens in self.tokenized]
        self.average_length = sum(self.lengths) / max(1, len(self.lengths))
        self.term_counts = [Counter(tokens) for tokens in self.tokenized]
        document_frequency: Counter[str] = Counter()
        for tokens in self.tokenized:
            document_frequency.update(set(tokens))
        count = max(1, len(documents))
        self.idf = {
            term: math.log(1 + (count - frequency + 0.5) / (frequency + 0.5))
            for term, frequency in document_frequency.items()
        }

    def search(self, query: str, limit: int) -> list[ScoredDocument]:
        query_terms = tokenize_for_search(query)
        scores: list[ScoredDocument] = []
        for index, term_count in enumerate(self.term_counts):
            score = 0.0
            length = self.lengths[index]
            for term in query_terms:
                frequency = term_count.get(term, 0)
                if frequency == 0:
                    continue
                denominator = frequency + self.k1 * (
                    1 - self.b + self.b * length / max(1.0, self.average_length)
                )
                score += self.idf.get(term, 0.0) * frequency * (self.k1 + 1) / denominator
            if score > 0:
                scores.append(ScoredDocument(index=index, score=score))
        return sorted(scores, key=lambda item: item.score, reverse=True)[:limit]
