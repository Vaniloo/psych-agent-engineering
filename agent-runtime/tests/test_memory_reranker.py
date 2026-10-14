from unittest.mock import AsyncMock

import httpx
import pytest

from psych_agent_runtime.config import Settings
from psych_agent_runtime.memory import MemoryService
from psych_agent_runtime.memory_reranker import MemoryReranker


@pytest.mark.asyncio
async def test_reranker_preserves_source_ids_and_rejects_incomplete_permutations():
    client = MemoryReranker("http://rank", "test")
    records = [
        {"id": "a", "role": "user", "content": "fact", "sessionId": 1},
        {"id": "b", "role": "user", "content": "correction", "sessionId": 2},
    ]
    ranking = [{"index": 1, "score": 0.9}, {"index": 0, "score": 0.2}]
    await client.client.aclose()
    client.client = httpx.AsyncClient(
        base_url="http://rank",
        transport=httpx.MockTransport(
            lambda request: httpx.Response(200, json={"ranking": ranking, "model": "original-0.6b"})
        ),
    )
    try:
        reordered, _ = await client.order("query", records)
        assert reordered == records[::-1] and reordered[0] is records[1]
        ranking.pop()
        with pytest.raises(ValueError, match="permutation"):
            await client.order("query", records)
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_reordered_pool_checks_later_conflicts_and_failure_keeps_original(tmp_path):
    service = MemoryService(
        Settings(
            memory_provider="laya",
            memory_contract_version="psych-memory-v4",
            memory_store_path=str(tmp_path / "index.db"),
        )
    )
    records = [{"id": str(i), "role": "user", "content": f"fact-{i}"} for i in range(8)]
    records[-1]["content"] = "later correction conflicts"
    service.store.candidates = lambda *args: records
    service.reranker = AsyncMock()
    service.reranker.order.return_value = (records, "original-0.6b")
    seen = []

    async def evaluate(state, tasks):
        if tasks == ["relevance"]:
            return {
                "decisions": {"relevance": {"choice": "yes", "accepted": True, "probability": 0.99}}
            }
        seen.extend(state["evidence"])
        return {"decisions": {"evidence_sufficient": {"choice": "no", "accepted": True}}}

    service.controller.evaluate = evaluate
    try:
        result = await service.retrieve("alice", "query")
        assert result["rounds"] == 2 and result["status"] == "controlled"
        assert len(seen) == 8 and seen[-1]["content"] == "later correction conflicts"
        service.reranker.order.side_effect = httpx.ConnectError("offline")
        result = await service.retrieve("alice", "query")
        assert result["rerank"]["status"] == "fallback" and result["evidence"] == records[:6]
    finally:
        await service.close()
