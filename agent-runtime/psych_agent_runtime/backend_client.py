from __future__ import annotations

from typing import Any

import httpx

from .config import Settings
from .schemas import AgentContext, MemoryCompression


class BackendClient:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._client = httpx.AsyncClient(
            base_url=settings.backend_base_url.rstrip("/"),
            headers={"X-Internal-Service-Token": settings.internal_service_token},
            timeout=httpx.Timeout(30),
            trust_env=False,
        )

    async def close(self) -> None:
        await self._client.aclose()

    async def risk_scan(self, text: str) -> dict[str, Any]:
        return await self._post("/internal/agent/risk-scan", {"text": text})

    async def context(self, username: str, session_id: int) -> AgentContext:
        response = await self._client.get(
            "/internal/agent/context",
            params={"username": username, "sessionId": session_id},
        )
        response.raise_for_status()
        return AgentContext.model_validate(response.json())

    async def persist_turn(
        self,
        *,
        username: str,
        session_id: int,
        user_message: str,
        assistant_reply: str,
        compression: MemoryCompression,
    ) -> dict[str, Any]:
        return await self._post(
            "/internal/agent/persist-turn",
            {
                "username": username,
                "sessionId": session_id,
                "userMessage": user_message,
                "assistantReply": assistant_reply,
                "sessionSummary": compression.session_summary,
                "mediumTermSummary": compression.medium_term_summary,
                "longTermMemory": compression.long_term_memory,
                "profilePatch": compression.profile_patch,
            },
        )

    async def crisis(self, username: str, message: str) -> dict[str, Any]:
        return await self._post(
            "/internal/agent/crisis",
            {"username": username, "message": message},
        )

    async def dashboard(self, username: str) -> dict[str, Any]:
        response = await self._client.get(
            "/internal/agent/dashboard",
            params={"username": username},
        )
        response.raise_for_status()
        return response.json()

    async def _post(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        response = await self._client.post(path, json=payload)
        response.raise_for_status()
        return response.json()
