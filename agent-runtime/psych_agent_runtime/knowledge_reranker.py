"""Bounded, optional knowledge ordering; failures retain the hybrid ranking."""
import asyncio
import math

import httpx


class KnowledgeReranker:
    def __init__(self, url, key, revision, timeout=3.0):
        self.revision = revision
        self.timeout = timeout
        self.client = httpx.AsyncClient(
            base_url=url.rstrip("/"), trust_env=False, timeout=timeout,
            headers={"Authorization": "Bearer " + key} if key else {},
        )

    async def close(self):
        await self.client.aclose()

    async def scores(self, query, candidates):
        async with asyncio.timeout(self.timeout):
            response = await self.client.post("/v1/rerank", json={
                "query": query,
                "candidates": [{"content": c.content} for c in candidates],
            })
            response.raise_for_status()
        payload = response.json()
        if (payload.get("model") != "Qwen/Qwen3-Reranker-0.6B"
                or payload.get("task") != "knowledge"
                or not self.revision or payload.get("revision") != self.revision):
            raise ValueError("knowledge reranker identity mismatch")
        ranking = payload.get("ranking", [])
        indices = [r["index"] for r in ranking]
        if (any(type(i) is not int for i in indices)
                or sorted(indices) != list(range(len(candidates)))
                or any(type(r["score"]) not in {int, float}
                       or not math.isfinite(r["score"]) or not 0 <= r["score"] <= 1
                       for r in ranking)):
            raise ValueError("reranker must return a full finite permutation")
        return {candidates[r["index"]].id: r["score"] for r in ranking}
