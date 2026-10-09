import asyncio
import hashlib
import json
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest

from psych_agent_runtime.config import Settings
from psych_agent_runtime.decision_router import DecisionRouter
from psych_agent_runtime.graph import AgentGraphRuntime
from psych_agent_runtime.planner import AgentPlanner
from psych_agent_runtime.route_policy import plan_for_route
from psych_agent_runtime.routing_contract import CONTRACT_VERSION, ROUTES, routing_state
from psych_agent_runtime.schemas import AgentRequest


def decision(route="strategy", probability=0.95, **changes):
    return {
        "contractVersion": CONTRACT_VERSION,
        "route": route,
        "probabilities": {
            name: probability if name == route else (1 - probability) / 5 for name in ROUTES
        },
        "threshold": 0.8,
        "calibrated": True,
        "accepted": True,
        **changes,
    }


@pytest.mark.parametrize(
    "changes",
    [
        {"probability": 0.7},
        {"calibrated": False},
        {"accepted": False},
    ],
)
def test_client_rejects_uncertain_or_uncalibrated_decisions(changes):
    async def run():
        router = DecisionRouter(Settings())
        await router.client.aclose()
        router.client = httpx.AsyncClient(
            base_url="http://router",
            transport=httpx.MockTransport(lambda _: httpx.Response(200, json=decision(**changes))),
        )
        try:
            assert not (await router.decide("帮我安排练习", [], ""))["accepted"]
        finally:
            await router.close()

    asyncio.run(run())


@pytest.mark.parametrize(
    "changes",
    [
        {"contractVersion": "wrong"},
        {"threshold": float("nan")},
        {"probabilities": {"chat": 1}},
        {"route": "execute_shell"},
    ],
)
def test_client_rejects_invalid_contract(changes):
    async def run():
        router = DecisionRouter(Settings())
        await router.client.aclose()
        # JSON encoding deliberately permits NaN to test the client's validation.
        router.client = httpx.AsyncClient(
            base_url="http://router",
            transport=httpx.MockTransport(
                lambda _: httpx.Response(200, content=json.dumps(decision(**changes)))
            ),
        )
        try:
            with pytest.raises(ValueError):
                await router.decide("你好", [], "")
        finally:
            await router.close()

    asyncio.run(run())


def test_bounded_state_omits_identity_and_internal_fields():
    state = routing_state(
        "a" * 5000,
        [
            {"role": "system", "content": "secret"},
            {"role": "user", "content": "x" * 500, "username": "private"},
        ],
        "s" * 500,
    )
    assert len(state["message"]) == 4000
    assert state["history"] == [{"role": "user", "content": "x" * 300}]
    assert len(state["session_summary"]) == 400


def test_accepted_route_uses_application_owned_tools():
    async def run():
        ollama = AsyncMock()
        planner = AgentPlanner(ollama, Settings(router_provider="laya"))
        planner.router.decide = AsyncMock(
            return_value={
                "route": "strategy",
                "accepted": True,
                "tools": [{"name": "execute_shell", "arguments": {"cmd": "bad"}}],
            }
        )
        try:
            plan = await planner.plan("帮我制定步骤")
            assert plan.tools == [
                {"name": "search_knowledge", "arguments": {"query": "帮我制定步骤", "limit": 6}}
            ]
            ollama.chat_json.assert_not_called()
            assert AgentGraphRuntime._after_planner({"plan": plan.model_dump()}) == "strategy"
        finally:
            await planner.close()

    asyncio.run(run())


def test_router_failure_records_fallback_and_preserves_bounded_tool_policy():
    async def run():
        planner = AgentPlanner(AsyncMock(), Settings(router_provider="laya"))
        planner.router.decide = AsyncMock(side_effect=httpx.ConnectError("offline"))
        try:
            plan = await planner.plan("看看我的报告")
            assert plan.intent == "dashboard"
            assert plan.tools == [{"name": "get_dashboard", "arguments": {}}]
            assert plan.routing["fallback"] == "router_unavailable_or_invalid"
            assert plan.routing["effectiveRoute"] == "dashboard"
        finally:
            await planner.close()

    asyncio.run(run())


def test_direct_routes_do_not_call_tools():
    for route in ("chat", "memory", "clarify"):
        plan = plan_for_route(route, "你好", "test")
        assert plan.tools == []
        assert AgentGraphRuntime._after_planner({"plan": plan.model_dump()}) == "direct"


@pytest.mark.parametrize("route", list(ROUTES))
def test_compiled_graph_executes_only_the_selected_capability(route):
    async def run():
        runtime = AgentGraphRuntime.__new__(AgentGraphRuntime)
        runtime.settings = Settings()
        calls = []

        async def call(name, arguments):
            calls.append(name)
            return {
                "risk_scan": {"highRisk": False},
                "get_user_context": {"username": "alice", "sessionId": 1},
                "search_knowledge": {"hits": [], "metrics": {}},
                "get_dashboard": {"reports": []},
                "persist_turn": {},
            }[name]

        runtime.tools = AsyncMock()
        runtime.tools.call.side_effect = call
        runtime.planner = AsyncMock()
        runtime.planner.plan.return_value = plan_for_route(route, "测试请求", "test")
        runtime.planner.assess_risk.return_value = {"riskBand": "low", "accepted": True}
        runtime._respond = AsyncMock(return_value={"reply": "测试回复"})
        runtime._compress_memory = AsyncMock(return_value={"compression": {}})
        runtime.graph = runtime._build_graph().compile()
        result = await runtime.invoke(
            AgentRequest(
                username="alice", sessionId=1, message="测试请求", requestId="routing-test"
            )
        )
        assert calls[:2] == ["risk_scan", "get_user_context"]
        expected_tools = [item["name"] for item in plan_for_route(route, "测试请求", "test").tools]
        assert calls[2:-1] == expected_tools
        assert calls[-1] == "persist_turn"
        assert result.reply == "测试回复"

    asyncio.run(run())


def test_crisis_bypasses_planner_and_resets_checkpoint_state():
    from langgraph.checkpoint.memory import InMemorySaver

    async def run():
        runtime = AgentGraphRuntime.__new__(AgentGraphRuntime)
        runtime.settings = Settings()
        high_risk = True
        calls = []

        async def call(name, arguments):
            calls.append(name)
            return {
                "risk_scan": {"highRisk": high_risk},
                "get_user_context": {"username": "alice", "sessionId": 1},
                "crisis_support": {"reply": "危机支持", "resources": []},
                "persist_turn": {},
            }[name]

        runtime.tools = AsyncMock()
        runtime.tools.call.side_effect = call
        runtime.planner = AsyncMock()
        runtime.planner.plan.return_value = plan_for_route("chat", "你好", "test")
        runtime._respond = AsyncMock(return_value={"reply": "普通回复"})
        runtime._compress_memory = AsyncMock(return_value={"compression": {}})
        runtime.graph = runtime._build_graph().compile(checkpointer=InMemorySaver())
        request = AgentRequest(
            username="alice", sessionId=1, message="测试", requestId="crisis-test"
        )
        first = await runtime.invoke(request)
        assert first.crisis
        runtime.planner.plan.assert_not_called()
        assert calls == ["risk_scan", "crisis_support", "persist_turn"]
        high_risk = False
        second = await runtime.invoke(request.model_copy(update={"request_id": "next-test"}))
        assert not second.crisis
        assert second.reply == "普通回复"
        assert second.resources == []
        assert second.analyze_response is None
        runtime.planner.plan.assert_called_once()

    asyncio.run(run())


def test_service_authentication_contract_and_disabled_checkpoint(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from psych_agent_runtime.routing_service import create_app

    weights = b"fake model for boundary tests"
    (tmp_path / "model.safetensors").write_bytes(weights)
    metadata = {
        "contractVersion": CONTRACT_VERSION,
        "model": "test",
        "amp": "fp16",
        "weightsSha256": hashlib.sha256(weights).hexdigest(),
        "calibrated": True,
        "enabled": False,
        "threshold": 0.85,
    }
    (tmp_path / "deployment.json").write_text(json.dumps(metadata))
    agent = SimpleNamespace(
        predict=lambda *_: {
            "answers": {
                "route": {"choice": "strategy", "probabilities": decision()["probabilities"]}
            }
        }
    )
    monkeypatch.setitem(sys.modules, "laya", SimpleNamespace(load=lambda *_a, **_k: agent))
    monkeypatch.setitem(sys.modules, "torch", SimpleNamespace(set_num_threads=lambda _: None))
    monkeypatch.setenv("ROUTER_CHECKPOINT", str(tmp_path))
    monkeypatch.setenv("ROUTER_API_KEY", "test-token")
    monkeypatch.setenv("ROUTER_HOST", "0.0.0.0")
    monkeypatch.setenv("ROUTER_DEVICE", "cpu")
    payload = {"contractVersion": CONTRACT_VERSION, "state": {"message": "帮我制定步骤"}}
    with TestClient(create_app()) as client:
        assert client.get("/health").json()["ready"]
        assert client.post("/v1/route", json=payload).status_code == 401
        headers = {"Authorization": "Bearer test-token"}
        result = client.post("/v1/route", json=payload, headers=headers)
        assert result.status_code == 200
        assert result.json()["route"] == "strategy"
        assert not result.json()["accepted"]
        assert (
            client.post(
                "/v1/route", json={**payload, "contractVersion": "wrong"}, headers=headers
            ).status_code
            == 409
        )
