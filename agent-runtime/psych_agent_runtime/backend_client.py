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

    async def dashboard(self, username: str) -> dict[str, Any]:
        response = await self._client.get(
            "/internal/agent/dashboard",
            params={"username": username},
        )
        response.raise_for_status()
        return response.json()

    async def goals(self, username: str) -> dict:
        response = await self._client.get("/internal/agent/goals", params={"username": username})
        response.raise_for_status()
        return response.json()

    async def create_goal(self, *, username, session_id, request_id):
        return await self._post(
            "/internal/agent/goals/create",
            {
                "username": username,
                "sessionId": session_id,
                "requestId": request_id,
            },
            timeout=130,
        )

    async def sync_goal_progress(
        self, *, username, session_id, request_id, goal_id, action_id, version, outcome, quote
    ):
        return await self._post(
            "/internal/agent/goals/sync",
            {
                "username": username,
                "sessionId": session_id,
                "requestId": request_id,
                "goalId": goal_id,
                "actionId": action_id,
                "version": version,
                "outcome": outcome,
                "quote": quote,
            },
        )

    async def memory_jobs(self) -> list[dict]:
        response = await self._client.get("/internal/agent/memory-jobs")
        response.raise_for_status()
        return response.json()

    async def complete_memory_job(self, job_id: int):
        return await self._post(f"/internal/agent/memory-jobs/{job_id}/complete", {})

    async def support_resources(self) -> list[dict[str, Any]]:
        response = await self._client.get("/help/resources")
        response.raise_for_status()
        return response.json()

    async def _post(self, path: str, payload: dict[str, Any], *, timeout: float | None = None) -> dict[str, Any]:
        kwargs = {"timeout": timeout} if timeout is not None else {}
        response = await self._client.post(path, json=payload, **kwargs)
        response.raise_for_status()
        return response.json()
