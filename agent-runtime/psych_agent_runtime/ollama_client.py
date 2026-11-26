from __future__ import annotations

from typing import Any

import httpx

from .config import Settings
from .telemetry import current_run


class OllamaClient:
    """Embeddings for knowledge retrieval. Chat goes through `llm.ChatModel`."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        if settings.ai_provider not in {"ollama", "deepseek"}:
            raise ValueError("unsupported AI_PROVIDER")
        self._client = httpx.AsyncClient(
            base_url=settings.ollama_base_url.rstrip("/"),
            timeout=httpx.Timeout(settings.ollama_timeout_seconds),
            trust_env=False,
        )

    async def close(self) -> None:
        await self._client.aclose()

    async def embed(
        self,
        texts: list[str],
        *,
        purpose: str = "embedding",
    ) -> list[list[float]]:
        if not texts:
            return []
        response = await self._client.post(
            "/api/embed",
            json={
                "model": self.settings.embedding_model,
                "input": texts,
                "truncate": True,
            },
        )
        response.raise_for_status()
        payload = response.json()
        self._record_usage(payload, purpose, self.settings.embedding_model)
        embeddings = payload.get("embeddings")
        if not isinstance(embeddings, list) or len(embeddings) != len(texts):
            raise RuntimeError("Ollama returned an invalid embedding payload")
        return embeddings

    async def health(self) -> bool:
        try:
            response = await self._client.get("/api/tags", timeout=5)
            return response.is_success
        except httpx.HTTPError:
            return False

    @staticmethod
    def _record_usage(payload: dict[str, Any], purpose: str, model: str) -> None:
        telemetry = current_run()
        if telemetry is not None:
            telemetry.record_model_call(
                purpose=purpose,
                model=model,
                payload=payload,
            )
