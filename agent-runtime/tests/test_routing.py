import asyncio
import hashlib
import json
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from benchmarks.runtime_fixture import make_runtime

from psych_agent_runtime.config import Settings
from psych_agent_runtime.decision_contract import ROUTER_CONTRACT as CONTRACT_VERSION
from psych_agent_runtime.decision_contract import ROUTER_QUESTIONS
from psych_agent_runtime.planner import AgentPlanner
from psych_agent_runtime.route_policy import plan_for_route
from psych_agent_runtime.routing_contract import ROUTES, routing_state
from psych_agent_runtime.schemas import AgentRequest


def decision(route="strategy", probability=0.95, **changes):
    route_item = {
        "choice": route,
        "probabilities": {
            name: probability if name == route else (1 - probability) / 5 for name in ROUTES
        },
        "threshold": 0.8,
        "calibrated": True,
        "accepted": True,
        **changes,
    }
    answers = {"route": route_item}
    for task, label in [
        ("conversation_kind", "counseling"),
        ("risk_band", "low"),
        ("support_need", "general"),
    ]:
        labels = ROUTER_QUESTIONS[task]["criteria"]
        answers[task] = {
            "choice": label,
            "probabilities": {
                name: 0.96 if name == label else 0.04 / (len(labels) - 1) for name in labels
            },
            "threshold": 0.8,
            "calibrated": True,
            "accepted": True,
        }
    return {"contractVersion": changes.get("contractVersion", CONTRACT_VERSION), "answers": answers}


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


@pytest.mark.asyncio
@pytest.mark.parametrize("message, route, tools", [
    ("帮我制定计划", "strategy", ["search_knowledge"]),
    ("看看我的报告", "dashboard", ["get_dashboard"]),
])
async def test_rule_router_selects_application_owned_tools(message, route, tools):
    plan = await AgentPlanner().plan(message)
    assert plan.intent == plan.routing["effectiveRoute"] == route
    assert plan.routing["provider"] == "rules"
    assert [tool["name"] for tool in plan.tools] == tools


def test_direct_routes_do_not_call_tools():
    for route in ("chat", "clarify"):
        plan = plan_for_route(route, "你好", "test")
        assert plan.tools == []
        assert plan.allowed_tools == []


@pytest.mark.parametrize("route", list(ROUTES))
def test_compiled_graph_executes_only_the_selected_capability(route):
    async def run():
        runtime = make_runtime()
        runtime.settings = Settings()
        calls = []

        async def call(name, arguments):
            calls.append(name)
            return {
                "risk_scan": {"highRisk": False},
                "get_user_context": {"username": "alice", "sessionId": 1},
                "search_knowledge": {"hits": [], "metrics": {}},
                "get_dashboard": {"reports": []},
                "memory_search": {"evidence": []},
                "persist_turn": {},
            }[name]

        runtime.tools = AsyncMock()
        runtime.tools.call.side_effect = call
        runtime.planner = AsyncMock()
        runtime.planner.plan.return_value = plan_for_route(route, "测试请求", "test")
        runtime.planner.plan.return_value.routing["riskDecision"] = {
            "riskBand": "low",
            "accepted": True,
        }
        runtime._respond = AsyncMock(return_value={"reply": "测试回复"})
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
        runtime = make_runtime()
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

    from psych_agent_runtime.routing_service import create_app, questions_digest

    weights = b"fake model for boundary tests"
    (tmp_path / "model.safetensors").write_bytes(weights)
    metadata = {
        "contractVersion": CONTRACT_VERSION,
        "model": "test",
        "amp": "fp16",
        "weightsSha256": hashlib.sha256(weights).hexdigest(),
        "calibrated": True,
        "profile": "router",
        "questionsSha256": questions_digest(ROUTER_QUESTIONS),
        "tasks": {
            name: {"enabled": False, "calibrated": True, "threshold": 0.85, "temperature": 1.0}
            for name in ROUTER_QUESTIONS
        },
    }
    (tmp_path / "deployment.json").write_text(json.dumps(metadata))
    agent = SimpleNamespace(
        cfg={"max_len": 512, "head_max_len": 192},
        tok=SimpleNamespace(mask_token="[MASK]", encode=lambda _, **kwargs: [1]),
        predict=lambda *_: {
            "answers": {
                task: {"choice": item["choice"], "probabilities": item["probabilities"]}
                for task, item in decision()["answers"].items()
            }
        },
    )
    monkeypatch.setitem(sys.modules, "laya", SimpleNamespace(load=lambda *_a, **_k: agent))
    monkeypatch.setitem(sys.modules, "torch", SimpleNamespace(set_num_threads=lambda _: None))
    monkeypatch.setenv("DECISION_CHECKPOINT", str(tmp_path))
    monkeypatch.setenv("ROUTER_API_KEY", "test-token")
    monkeypatch.setenv("ROUTER_HOST", "0.0.0.0")
    monkeypatch.setenv("ROUTER_DEVICE", "cpu")
    payload = {
        "contractVersion": CONTRACT_VERSION,
        "state": {"message": "帮我制定步骤"},
        "tasks": list(ROUTER_QUESTIONS),
    }
    with TestClient(create_app()) as client:
        assert client.get("/health").json()["ready"]
        assert client.post("/v1/decisions", json=payload).status_code == 401
        headers = {"Authorization": "Bearer test-token"}
        result = client.post("/v1/decisions", json=payload, headers=headers)
        assert result.status_code == 200
        assert result.json()["answers"]["route"]["choice"] == "strategy"
        assert not result.json()["answers"]["route"]["accepted"]
        assert (
            client.post(
                "/v1/decisions", json={**payload, "contractVersion": "wrong"}, headers=headers
            ).status_code
            == 409
        )
