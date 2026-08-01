from __future__ import annotations

import json
import math
import statistics
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .config import Settings
from .rag import RagEngine
from .schemas import RagHit


class RagEvaluator:
    def __init__(self, settings: Settings, rag: RagEngine) -> None:
        self.settings = settings
        self.rag = rag

    async def evaluate(self) -> dict[str, Any]:
        dataset = self._load_dataset()
        rows: list[dict[str, Any]] = []
        latencies: list[float] = []
        cache_hits = 0

        for case in dataset["cases"]:
            result = await self.rag.search_with_metrics(
                case["query"],
                limit=5,
                bypass_cache=True,
            )
            relevance = self._relevance_vector(result.hits, case["relevant"])
            row = self._score_case(case, result.hits, relevance)
            row["latencyMs"] = result.metrics.duration_ms
            row["cacheHit"] = result.metrics.cache_hit
            rows.append(row)
            latencies.append(result.metrics.duration_ms)
            cache_hits += int(result.metrics.cache_hit)

        sample_count = len(rows)
        return {
            "datasetVersion": dataset["version"],
            "sampleCount": sample_count,
            "recallAt1": self._mean(rows, "recallAt1"),
            "recallAt3": self._mean(rows, "recallAt3"),
            "recallAt5": self._mean(rows, "recallAt5"),
            "precisionAt5": self._mean(rows, "precisionAt5"),
            "hitRateAt5": self._mean(rows, "hitAt5"),
            "mrr": self._mean(rows, "reciprocalRank"),
            "nDcgAt5": self._mean(rows, "nDcgAt5"),
            "avgLatencyMs": round(statistics.fmean(latencies), 2) if latencies else 0.0,
            "p95LatencyMs": self._percentile(latencies, 0.95),
            "cacheHitRate": round(cache_hits / sample_count, 4) if sample_count else 0.0,
            "cachePolicy": "bypass",
            "evaluatedAt": datetime.now(UTC).isoformat(),
            "cases": rows,
        }

    def _load_dataset(self) -> dict[str, Any]:
        path = Path(self.settings.rag_evaluation_dataset)
        if not path.is_absolute():
            path = Path(__file__).resolve().parents[1] / path
        with path.open(encoding="utf-8") as stream:
            dataset = json.load(stream)
        if not dataset.get("cases"):
            raise RuntimeError(f"RAG evaluation dataset is empty: {path}")
        return dataset

    @staticmethod
    def _relevance_vector(
        hits: list[RagHit],
        relevant_rules: list[dict[str, Any]],
    ) -> list[int]:
        relevance: list[int] = []
        matched_documents: set[str] = set()
        for hit in hits:
            matched = any(
                hit.category == rule["category"]
                and any(marker in hit.content for marker in rule.get("contains") or [])
                for rule in relevant_rules
            )
            is_new_relevant = matched and hit.document_id not in matched_documents
            relevance.append(int(is_new_relevant))
            if is_new_relevant:
                matched_documents.add(hit.document_id)
        return relevance

    def _score_case(
        self,
        case: dict[str, Any],
        hits: list[RagHit],
        relevance: list[int],
    ) -> dict[str, Any]:
        relevant_total = max(1, len(case["relevant"]))
        first_rank = next(
            (index for index, value in enumerate(relevance, start=1) if value),
            None,
        )
        return {
            "id": case["id"],
            "query": case["query"],
            "recallAt1": self._recall(relevance, relevant_total, 1),
            "recallAt3": self._recall(relevance, relevant_total, 3),
            "recallAt5": self._recall(relevance, relevant_total, 5),
            "precisionAt5": round(sum(relevance[:5]) / 5, 4),
            "hitAt5": int(any(relevance[:5])),
            "reciprocalRank": round(1 / first_rank, 4) if first_rank else 0.0,
            "nDcgAt5": self._ndcg(relevance, relevant_total, 5),
            "topCategories": [hit.category for hit in hits[:5]],
            "topSources": [hit.source for hit in hits[:5]],
        }

    @staticmethod
    def _recall(relevance: list[int], relevant_total: int, k: int) -> float:
        return round(min(relevant_total, sum(relevance[:k])) / relevant_total, 4)

    @staticmethod
    def _ndcg(relevance: list[int], relevant_total: int, k: int) -> float:
        dcg = sum(
            value / math.log2(index + 1)
            for index, value in enumerate(relevance[:k], start=1)
        )
        ideal = sum(
            1 / math.log2(index + 1)
            for index in range(1, min(relevant_total, k) + 1)
        )
        return round(dcg / ideal, 4) if ideal else 0.0

    @staticmethod
    def _mean(rows: list[dict[str, Any]], key: str) -> float:
        return round(statistics.fmean(float(row[key]) for row in rows), 4) if rows else 0.0

    @staticmethod
    def _percentile(values: list[float], quantile: float) -> float:
        if not values:
            return 0.0
        ordered = sorted(values)
        index = max(0, math.ceil(len(ordered) * quantile) - 1)
        return round(ordered[index], 2)
