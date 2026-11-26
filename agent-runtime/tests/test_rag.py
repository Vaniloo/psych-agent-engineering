from types import SimpleNamespace

import pytest

from psych_agent_runtime.config import Settings
from psych_agent_runtime.rag import RagEngine

DOCS = [
    ("sleep-1", "d-sleep", "sleep", "保持规律的起床时间有助于改善失眠。"),
    ("sleep-2", "d-sleep", "sleep", "睡前一小时减少屏幕使用，失眠时不要躺在床上硬睡。"),
    ("sleep-3", "d-sleep", "sleep", "失眠持续数周时建议咨询医生。"),
    ("anx-1", "d-anx", "anxiety", "缓慢呼吸可以帮助缓解焦虑带来的心慌。"),
    ("misc-1", "d-misc", "default", "这是一段与主题无关的说明。"),
]


class Collection:
    """Fake Chroma: dense distances are given per query; `get` calls are counted."""

    def __init__(self, distances):
        self.distances, self.gets = distances, 0

    def count(self):
        return len(DOCS)

    def get(self, **kwargs):
        self.gets += 1
        return {"ids": [d[0] for d in DOCS], "documents": [d[3] for d in DOCS],
                "metadatas": [{"document_id": d[1], "category": d[2], "source": f"https://x/{d[1]}"} for d in DOCS]}

    def query(self, *, query_embeddings, n_results, where, include):
        rows = [d for d in DOCS if not where or d[2] == where["category"]]
        rows = sorted(rows, key=lambda d: self.distances.get(d[0], 1.0))[:n_results]
        return {"ids": [[d[0] for d in rows]], "documents": [[d[3] for d in rows]],
                "metadatas": [[{"document_id": d[1], "category": d[2], "source": f"https://x/{d[1]}"} for d in rows]],
                "distances": [[self.distances.get(d[0], 1.0) for d in rows]]}


def engine(distances):
    async def embed(texts, purpose):
        return [[0.0] for _ in texts]
    rag = RagEngine(Settings(), SimpleNamespace(embed=embed))
    rag._collection = Collection(distances)
    rag._redis = None

    async def no_redis():
        return None
    rag._ensure_redis = no_redis
    return rag


@pytest.mark.asyncio
async def test_rrf_fuses_both_routes_and_caps_chunks_per_document():
    rag = engine({"sleep-1": 0.2, "sleep-2": 0.3, "sleep-3": 0.35, "anx-1": 0.9})
    result = await rag.search_with_metrics("失眠怎么办", limit=5)
    ids = [h.id for h in result.hits]
    assert ids[0] == "sleep-1" and result.hits[0].match_reason == "向量与关键词双路命中"
    assert sum(h.document_id == "d-sleep" for h in result.hits) == 2  # at most two chunks per document
    assert result.metrics.evidence == "strong"


@pytest.mark.asyncio
async def test_sparse_index_is_built_once_and_rebuilt_after_changes():
    rag = engine({"sleep-1": 0.2})
    await rag.search_with_metrics("失眠", bypass_cache=True)
    await rag.search_with_metrics("焦虑", bypass_cache=True)
    assert rag._collection.gets == 1
    rag._sparse = None  # what upsert_document / delete_document do
    await rag.search_with_metrics("失眠", bypass_cache=True)
    assert rag._collection.gets == 2


@pytest.mark.asyncio
async def test_category_filter_applies_to_both_routes():
    rag = engine({"anx-1": 0.2, "sleep-1": 0.1})
    result = await rag.search_with_metrics("失眠 焦虑", category="anxiety")
    assert {h.category for h in result.hits} == {"anxiety"}


@pytest.mark.asyncio
async def test_weak_vector_only_matches_are_dropped_and_reported():
    rag = engine({"misc-1": 0.95})  # similarity 0.05, no keyword overlap
    result = await rag.search_with_metrics("完全不相干的问题")
    assert "misc-1" not in [h.id for h in result.hits]
    weak = await engine({"anx-1": 0.7}).search_with_metrics("心慌")
    assert weak.hits and weak.metrics.evidence == "weak"
    none = await engine({}).search_with_metrics("zzzz")
    assert none.metrics.evidence == "none" and none.hits == []
