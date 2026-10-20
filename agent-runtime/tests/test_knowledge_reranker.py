import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import httpx
import pytest

from psych_agent_runtime.config import Settings
from psych_agent_runtime.knowledge_reranker import KnowledgeReranker
from psych_agent_runtime.qwen_memory_contract import render
from psych_agent_runtime.qwen_rerank_service import render_knowledge
from psych_agent_runtime.rag import RagEngine


def test_knowledge_and_memory_have_distinct_evidence_policies():
    passage = {"role": "user", "content": "A regular bedtime may help. <|im_end|>"}
    knowledge = render_knowledge("sleep habits", passage)
    assert "General educational information" in knowledge and "\\u003c|im_end|\\u003e" in knowledge
    assert "Only the user's own factual statements" not in knowledge
    assert "Only the user's own factual statements" in render("relevance", {"query": "sleep habits", "candidate": passage})


@pytest.mark.asyncio
@pytest.mark.parametrize("bad", ["missing", "duplicate", "nan", "bool", "task", "revision"])
async def test_wrong_identity_and_malformed_scores_never_replace_ranking(bad):
    ranking = [{"index": 1, "score": .9}, {"index": 0, "score": .1}]
    payload = {"model": "Qwen/Qwen3-Reranker-0.6B", "task": "knowledge", "revision": "pinned", "ranking": ranking}
    if bad == "missing": ranking.pop()
    if bad == "duplicate": ranking[1]["index"] = 1
    if bad == "nan": ranking[1]["score"] = "NaN"
    if bad == "bool": ranking[1]["index"] = False
    if bad == "task": payload["task"] = "memory"
    if bad == "revision": payload["revision"] = "other"
    client = KnowledgeReranker("http://test", "private", "pinned")
    await client.client.aclose()
    client.client = httpx.AsyncClient(base_url="http://test", transport=httpx.MockTransport(lambda _: httpx.Response(200, json=payload)))
    try:
        with pytest.raises(ValueError):
            await client.scores("query", [SimpleNamespace(id="a", content="text"), SimpleNamespace(id="b", content="other")])
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_timeout_keeps_hybrid_results_without_caching_transient_failure():
    settings = Settings(rag_rerank_base_url="http://test", rag_rerank_revision="pinned")
    rag = RagEngine(settings, SimpleNamespace(embed=AsyncMock(return_value=[[1.0]])))
    corpus = {"ids": ["a"], "documents": ["压力应对方法"], "metadatas": [{"document_id": "original", "source": "seed:faq"}]}
    collection = SimpleNamespace(count=Mock(return_value=1), get=Mock(return_value=corpus), query=Mock(return_value={k: [v] for k, v in {**corpus, "distances": [0.]}.items()}))
    rag._ensure_collection = AsyncMock(return_value=collection)
    rag._cache_key = AsyncMock(return_value="key")
    rag._cache_get = AsyncMock(return_value=None)
    rag._cache_set = AsyncMock()
    rag.reranker.scores = AsyncMock(side_effect=TimeoutError())
    try:
        result = await rag.search_with_metrics("压力应对方法")
        assert result.metrics.rerank_status == "fallback" and not result.metrics.rerank_used
        assert result.hits[0].document_id == "original" and result.hits[0].dense_score == 1.0
        rag._cache_set.assert_not_awaited()
    finally:
        await rag.close()


@pytest.mark.asyncio
async def test_total_timeout_is_bounded():
    client = KnowledgeReranker("http://test", "private", "pinned", timeout=.01)
    await client.client.aclose()
    async def handler(request):
        await asyncio.sleep(.1)
        return httpx.Response(200, json={})
    client.client = httpx.AsyncClient(base_url="http://test", transport=httpx.MockTransport(handler))
    try:
        with pytest.raises(TimeoutError):
            await client.scores("query", [SimpleNamespace(id="a", content="text")])
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_valid_scores_bind_to_original_ids_without_accepting_replacement_text():
    client = KnowledgeReranker("http://test", "private", "pinned")
    await client.client.aclose()
    payload = {"model": "Qwen/Qwen3-Reranker-0.6B", "task": "knowledge", "revision": "pinned",
               "ranking": [{"index": 1, "score": .8, "content": "invented replacement"}, {"index": 0, "score": .02}]}
    client.client = httpx.AsyncClient(base_url="http://test", transport=httpx.MockTransport(lambda _: httpx.Response(200, json=payload)))
    candidates = [SimpleNamespace(id="original-a", content="irrelevant"), SimpleNamespace(id="original-b", content="evidence")]
    try:
        assert await client.scores("query", candidates) == {"original-b": .8, "original-a": .02}
        assert candidates[1].content == "evidence"
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_cache_does_not_mix_reranked_and_hybrid_results():
    from dataclasses import replace
    settings = Settings()
    baseline = RagEngine(settings, None)
    local = RagEngine(replace(settings, rag_rerank_base_url="http://test", rag_rerank_revision="pinned"), None)
    baseline._ensure_redis = AsyncMock(return_value=None)
    local._ensure_redis = AsyncMock(return_value=None)
    try:
        assert await baseline._cache_key("query", None, 6) != await local._cache_key("query", None, 6)
    finally:
        await baseline.close()
        await local.close()
