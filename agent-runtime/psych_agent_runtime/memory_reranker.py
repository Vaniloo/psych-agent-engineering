"""Optional local Qwen ordering. Never removes or invents source records."""

import math

import httpx


class MemoryReranker:
    def __init__(self, url, key, timeout=2):
        self.client = httpx.AsyncClient(
            base_url=url.rstrip("/"),
            trust_env=False,
            timeout=timeout,
            headers={"Authorization": f"Bearer {key}"} if key else {},
        )

    async def close(self):
        await self.client.aclose()

    async def order(self, query, candidates):
        response = await self.client.post(
            "/v1/rerank",
            json={
                "query": query,
                "candidates": [{"role": c["role"], "content": c["content"]} for c in candidates],
            },
        )
        response.raise_for_status()
        payload = response.json()
        ranking = payload["ranking"]
        indices = [r["index"] for r in ranking]
        if (
            any(type(i) is not int for i in indices)
            or sorted(indices) != list(range(len(candidates)))
            or any(
                not isinstance(r["score"], (int, float))
                or not math.isfinite(r["score"])
                or not 0 <= r["score"] <= 1
                for r in ranking
            )
        ):
            raise ValueError("reranker must return a full finite permutation")
        return [candidates[i] for i in indices], payload.get("model", "unknown")
