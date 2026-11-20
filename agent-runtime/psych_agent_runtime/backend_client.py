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

    async def model_api(self, username):
        return await self._get("/internal/agent/model-api", params={"username": username})

    async def risk_scan(self, text: str) -> dict[str, Any]:
        return await self._post("/internal/agent/risk-scan", {"text": text})

    async def context(self, username: str, session_id: int) -> AgentContext:
        context = await self._get(
            "/internal/agent/context",
            params={"username": username, "sessionId": session_id},
        )
        return AgentContext.model_validate(context)

    async def begin_turn(self, *, username, session_id, user_message, request_id):
        return await self._post("/internal/agent/begin-turn", {
            "username": username, "sessionId": session_id,
            "userMessage": user_message, "requestId": request_id,
        })

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

    async def dashboard(self, username: str) -> dict[str, Any]:
        response = await self._client.get(
            "/internal/agent/dashboard",
            params={"username": username},
        )
        response.raise_for_status()
        return response.json()

    async def goals(self, username: str, title: str | None = None, status: str | None = None,
                    session_id: int | None = None, request_id: str | None = None) -> dict:
        params = {"username": username}
        if title:
            params["title"] = title
        if status:
            params["status"] = status
        if session_id is not None:
            params["sessionId"] = session_id
        if request_id is not None:
            params["requestId"] = request_id
        return await self._get("/internal/agent/goals", params=params)

    async def create_goal(self, *, username, session_id, request_id, source_goal_title=None, model_approval=None):
        return await self._post(
            "/internal/agent/goals/create",
            {
                "username": username,
                "sessionId": session_id,
                "requestId": request_id,
                "sourceGoalTitle": source_goal_title,
                **({"modelApproval": model_approval} if model_approval else {}),
            },
            timeout=130,
        )

    async def sync_goal_progress(
        self, *, username, session_id, request_id, goal_id, action_id, version, outcome, quote, model_approval=None
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
                **({"modelApproval": model_approval} if model_approval else {}),
            },
        )

    async def manage_goal(self, *, username, session_id, request_id, goal_id, version, operation, edits_json, model_approval):
        return await self._post("/internal/agent/goals/manage", {
            "username": username, "sessionId": session_id, "requestId": request_id, "goalId": goal_id,
            "version": version, "operation": operation, "editsJson": edits_json, "modelApproval": model_approval,
        })

    async def advance_goal(self, *, username, session_id, request_id, goal_id, check_in_id):
        return await self._post("/internal/agent/goals/advance", {
            "username": username, "sessionId": session_id, "requestId": request_id,
            "goalId": goal_id, "checkInId": check_in_id,
        }, timeout=40)

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
