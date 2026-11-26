from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any

import httpx

from .config import Settings
from .telemetry import current_run


class OllamaClient:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        if settings.ai_provider not in {"ollama", "deepseek"}:
            raise ValueError("unsupported AI_PROVIDER")
        from .deepseek_client import DeepSeekClient
        self._generation = DeepSeekClient(settings) if settings.ai_provider == "deepseek" else None
        self._client = httpx.AsyncClient(
            base_url=settings.ollama_base_url.rstrip("/"),
            timeout=httpx.Timeout(settings.ollama_timeout_seconds),
            trust_env=False,
        )

    async def close(self) -> None:
        await self._client.aclose()
        if self._generation:
            await self._generation.close()

    async def chat(
        self,
        messages: list[dict[str, str]],
        *,
        temperature: float = 0.3,
        purpose: str = "chat",
    ) -> str:
        if self._generation:
            return await self._generation.chat(messages, temperature=temperature, purpose=purpose)
        response = await self._client.post(
            "/api/chat",
            json={
                "model": self.settings.chat_model,
                "messages": messages,
                "stream": False,
                "think": self.settings.ollama_think,
                "options": {
                    "temperature": temperature,
                    "num_ctx": self.settings.context_window_tokens,
                },
            },
        )
        response.raise_for_status()
        payload = response.json()
        self._record_usage(payload, purpose, self.settings.chat_model)
        return str(payload.get("message", {}).get("content", "")).strip()

    async def chat_json(
        self,
        messages: list[dict[str, str]],
        *,
        temperature: float = 0.0,
        purpose: str = "structured",
    ) -> dict[str, Any]:
        if self._generation:
            return await self._generation.chat_json(messages, temperature=temperature, purpose=purpose)
        response = await self._client.post(
            "/api/chat",
            json={
                "model": self.settings.chat_model,
                "messages": messages,
                "format": "json",
                "stream": False,
                "think": self.settings.ollama_think,
                "options": {
                    "temperature": temperature,
                    "num_ctx": self.settings.context_window_tokens,
                },
            },
        )
        response.raise_for_status()
        payload = response.json()
        self._record_usage(payload, purpose, self.settings.chat_model)
        content = str(payload.get("message", {}).get("content", "")).strip()
        try:
            return json.loads(content)
        except json.JSONDecodeError:
            start = content.find("{")
            end = content.rfind("}")
            if start < 0 or end < start:
                raise
            return json.loads(content[start : end + 1])

    async def stream_chat(
        self,
        messages: list[dict[str, str]],
        *,
        temperature: float = 0.3,
        purpose: str = "response",
    ) -> AsyncIterator[str]:
        if self._generation:
            async for token in self._generation.stream_chat(messages, temperature=temperature, purpose=purpose):
                yield token
            return
        async with self._client.stream(
            "POST",
            "/api/chat",
            json={
                "model": self.settings.chat_model,
                "messages": messages,
                "stream": True,
                "think": self.settings.ollama_think,
                "options": {
                    "temperature": temperature,
                    "num_ctx": self.settings.context_window_tokens,
                },
            },
        ) as response:
            response.raise_for_status()
            async for line in response.aiter_lines():
                if not line:
                    continue
                payload = json.loads(line)
                token = payload.get("message", {}).get("content")
                if token:
                    yield str(token)
                if payload.get("done"):
                    self._record_usage(payload, purpose, self.settings.chat_model)
                    break

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
