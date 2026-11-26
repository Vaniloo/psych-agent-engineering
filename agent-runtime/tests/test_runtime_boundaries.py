import asyncio
import json
from unittest.mock import AsyncMock

import httpx
from fastapi import FastAPI
from fastapi.testclient import TestClient

from benchmarks.loop_probe import run_controller
from benchmarks.runtime_fixture import make_runtime

from psych_agent_runtime.backend_client import BackendClient
from psych_agent_runtime.config import Settings
from psych_agent_runtime.mcp_client import McpToolClient
from psych_agent_runtime.planner import AgentPlanner
from psych_agent_runtime.route_policy import plan_for_route
from psych_agent_runtime.schemas import AgentRequest, RagSearchResult
from psych_agent_runtime.service_auth import InternalAuthMiddleware
from psych_agent_runtime.tool_controller import ToolController


def test_internal_identity_surface_requires_service_credentials():
    app = FastAPI()
    app.add_middleware(InternalAuthMiddleware, token="service-secret")

    @app.get("/identity")
    def identity():
        return {"accepted": True}

    with TestClient(app) as client:
        assert client.get("/identity").status_code == 401
        assert (
            client.get("/identity", headers={"X-Internal-Service-Token": "wrong"}).status_code
            == 401
        )
        assert (
            client.get(
                "/identity", headers={"X-Internal-Service-Token": "service-secret"}
            ).status_code
            == 200
        )


def test_executor_stops_at_budget_and_observes_previous_results():
    async def run():
        controller = ToolController(Settings(executor_provider="public_qwen"))
        observed = []

        async def decide(state, tasks):
            observed.append(state)
            action = state["allowedTools"][0]
            return {"decisions": {"next_action": {"accepted": True, "choice": action}}}

        controller.model.evaluate = decide
        invoke = AsyncMock(side_effect=lambda name: {"capability": name})
        try:
            results, trace = await run_controller(
                controller,
                plan_for_route("strategy", "帮我整理行动", "test"),
                "帮我整理行动",
                invoke,
                lambda *_: None,
            )
            assert len(results) == invoke.await_count == 2
            assert len(observed) == 2
            assert list(observed[1]["observations"]) == [next(iter(results))]
            assert trace[-1]["fallback"] == "budget_exhausted"
        finally:
            await controller.close()

    asyncio.run(run())


def test_real_capability_and_backend_adapters_in_compiled_graph():
    async def run():
        settings = Settings(internal_service_token="trusted")
        backend = BackendClient(settings)
        seen = []

        def respond(request):
            assert request.headers["X-Internal-Service-Token"] == "trusted"
            seen.append(request)
            path = request.url.path
            if path.endswith("risk-scan"):
                return httpx.Response(200, json={"highRisk": False})
            if path.endswith("context"):
                assert request.url.params["username"] == "alice"
                return httpx.Response(200, json={"username": "alice", "sessionId": 1})
            if path.endswith("persist-turn"):
                saved = json.loads(request.content)
                assert saved["requestId"] == "round-trip"
                assert saved["username"] == "alice"
                assert saved["assistantReply"] == "回应"
                return httpx.Response(200, json={"saved": True})
            raise AssertionError(path)

        await backend._client.aclose()
        backend._client = httpx.AsyncClient(
            base_url="http://backend",
            transport=httpx.MockTransport(respond),
            headers={"X-Internal-Service-Token": "trusted"},
        )
        runtime = make_runtime()
        runtime.settings = settings
        rag = AsyncMock()
        rag.search_with_metrics.return_value = RagSearchResult()
        runtime.tools = McpToolClient(settings, backend, rag)
        runtime.planner = AgentPlanner()
        runtime.planner.plan = AsyncMock(
            return_value=plan_for_route(
                "strategy",
                "帮我制定计划",
                "fixed low-risk round-trip fixture",
            )
        )
        runtime._respond = AsyncMock(return_value={"reply": "回应"})
        runtime.graph = runtime._build_graph().compile()
        try:
            result = await runtime.invoke(
                AgentRequest(
                    username="alice", sessionId=1, message="帮我制定计划", requestId="round-trip"
                )
            )
            assert result.reply == "回应"
            assert [request.url.path for request in seen] == [
                "/internal/agent/risk-scan",
                "/internal/agent/context",
                "/internal/agent/persist-turn",
            ]
            rag.search_with_metrics.assert_awaited_once()
            assert result.metrics.persistence_status == "saved"
        finally:

            await backend.close()

    asyncio.run(run())
