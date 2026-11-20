import asyncio
import hashlib
import json
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from benchmarks.loop_probe import run_controller
from benchmarks.runtime_fixture import make_runtime

from psych_agent_runtime.backend_client import BackendClient
from psych_agent_runtime.config import Settings
from psych_agent_runtime.decision_contract import EXECUTOR_CONTRACT, EXECUTOR_QUESTIONS
from psych_agent_runtime.mcp_client import McpToolClient
from psych_agent_runtime.planner import AgentPlanner
from psych_agent_runtime.route_policy import plan_for_route
from psych_agent_runtime.routing_service import (
    calibrate_probabilities,
    create_app,
    questions_digest,
)
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


def test_temperature_is_applied_before_acceptance():
    result = calibrate_probabilities({"a": 0.9, "b": 0.1}, {"calibrated": True, "temperature": 2})
    assert result["a"] == pytest.approx(0.75)
    for temperature in (0, -1, float("nan"), float("inf")):
        with pytest.raises(ValueError):
            calibrate_probabilities({"a": 0.9}, {"calibrated": True, "temperature": temperature})


def test_executor_worker_masks_choices_and_refuses_truncated_input(tmp_path, monkeypatch):
    weights = b"boundary fixture"
    (tmp_path / "model.safetensors").write_bytes(weights)
    metadata = {
        "profile": "executor",
        "contractVersion": EXECUTOR_CONTRACT,
        "questionsSha256": questions_digest(EXECUTOR_QUESTIONS),
        "amp": "fp16",
        "weightsSha256": hashlib.sha256(weights).hexdigest(),
        "maxStateTokens": 256,
        "tasks": {
            "next_action": {
                "enabled": True,
                "calibrated": True,
                "temperature": 2,
                "threshold": 0.85,
            }
        },
    }
    (tmp_path / "deployment.json").write_text(json.dumps(metadata))
    seen = []

    def predict(state, questions):
        seen.append(questions)
        return {
            "answers": {
                "next_action": {
                    "choice": "get_dashboard",
                    "probabilities": {"get_dashboard": 0.9, "finish": 0.05, "clarify": 0.05},
                }
            }
        }

    agent = SimpleNamespace(
        cfg={"max_len": 512, "head_max_len": 192},
        tok=SimpleNamespace(mask_token="[MASK]", encode=lambda text, **_: list(text)),
        predict=predict,
    )
    monkeypatch.setitem(sys.modules, "laya", SimpleNamespace(load=lambda *a, **k: agent))
    monkeypatch.setitem(sys.modules, "torch", SimpleNamespace(set_num_threads=lambda _: None))
    monkeypatch.setenv("DECISION_PROFILE", "executor")
    monkeypatch.setenv("DECISION_CHECKPOINT", str(tmp_path))
    monkeypatch.setenv("DECISION_API_KEY", "secret")
    monkeypatch.setenv("ROUTER_DEVICE", "cpu")
    payload = {
        "contractVersion": EXECUTOR_CONTRACT,
        "state": {"allowedTools": ["get_dashboard"]},
        "tasks": ["next_action"],
    }
    headers = {"Authorization": "Bearer secret"}
    with TestClient(create_app()) as client:
        result = client.post("/v1/decisions", json=payload, headers=headers)
        assert result.status_code == 200
        assert set(seen[0]["next_action"]["criteria"]) == {"get_dashboard", "finish", "clarify"}
        assert not result.json()["answers"]["next_action"]["accepted"]
        assert (
            client.post(
                "/v1/decisions",
                headers=headers,
                json={
                    **payload,
                    "state": {"allowedTools": ["get_dashboard"], "request": "x" * 400},
                },
            ).status_code
            == 422
        )
        assert len(seen) == 1
        assert (
            client.post(
                "/v1/decisions",
                headers=headers,
                json={**payload, "state": {"allowedTools": ["persist_turn"]}},
            ).status_code
            == 422
        )
    metadata["maxStateTokens"] = 9999
    (tmp_path / "deployment.json").write_text(json.dumps(metadata))
    with pytest.raises(ValueError, match="encoder capacity"), TestClient(create_app()):
        pass


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
            if path.endswith("goals"):
                assert request.url.params["username"] == "alice"
                return httpx.Response(200, json={"goals": []})
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
                {"riskDecision": {"accepted": True, "riskBand": "low"}},
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
                "/internal/agent/goals",
                "/internal/agent/persist-turn",
            ]
            rag.search_with_metrics.assert_awaited_once()
            assert result.metrics.persistence_status == "saved"
        finally:

            await backend.close()

    asyncio.run(run())
