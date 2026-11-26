from __future__ import annotations

from typing import Any

import httpx

from .config import Settings
from .schemas import AgentContext, MemoryCompression, MemoryEvidence


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
        context = await self._get(
            "/internal/agent/context",
            params={"username": username, "sessionId": session_id},
        )
        return AgentContext.model_validate(context)


    async def recent_messages(self, username):
        rows = await self._get("/internal/agent/recent-messages", params={"username": username}, timeout=3)
        if not isinstance(rows, list) or len(rows) > 24:
            raise TypeError("recent evidence must be a bounded list")
        return [MemoryEvidence.model_validate(r).model_dump() for r in rows]

    async def persist_turn(
        self,
        *,
        username: str,
        session_id: int,
        user_message: str,
        assistant_reply: str,
        compression: MemoryCompression,
        request_id: str,
    ) -> dict[str, Any]:
        return await self._post(
            "/internal/agent/persist-turn",
            {
                "requestId": request_id,
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

    async def memory_jobs(self) -> list[dict]:
        return await self._get("/internal/agent/memory-jobs")

    async def complete_memory_job(self, job_id: int):
        return await self._post(f"/internal/agent/memory-jobs/{job_id}/complete", {})

    async def support_resources(self) -> list[dict[str, Any]]:
        return await self._get("/help/resources")

    async def _get(self, path: str, **options) -> Any:
        response = await self._client.get(path, **options)
        response.raise_for_status()
        return response.json()

    async def _post(self, path: str, payload: dict[str, Any], *, timeout: float | None = None) -> dict[str, Any]:
        kwargs = {"timeout": timeout} if timeout is not None else {}
        response = await self._client.post(path, json=payload, **kwargs)
        response.raise_for_status()
        return response.json()
