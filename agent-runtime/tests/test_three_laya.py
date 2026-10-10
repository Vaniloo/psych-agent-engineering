import asyncio
import json
from unittest.mock import AsyncMock

import httpx
import pytest

from psych_agent_runtime.config import Settings
from psych_agent_runtime.decision_contract import ROUTER_CONTRACT, ROUTER_QUESTIONS
from psych_agent_runtime.graph import AgentGraphRuntime
from psych_agent_runtime.memory import MemoryService, MemoryStore
from psych_agent_runtime.planner import AgentPlanner
from psych_agent_runtime.route_policy import plan_for_route
from psych_agent_runtime.schemas import AgentRequest
from psych_agent_runtime.tool_controller import ToolController


def answer(label, labels, probability=0.96):
    return {
        "choice": label,
        "probabilities": {
            name: probability if name == label else (1 - probability) / (len(labels) - 1)
            for name in labels
        },
        "calibrated": True,
        "accepted": True,
        "threshold": 0.85,
    }


@pytest.mark.parametrize(
    "kind,band,probability,professional,expected,message",
    [
        ("chat", "low", 0.96, False, [], "测试"),
        ("counseling", "low", 0.96, False, ["search_knowledge"], "测试"),
        (
            "counseling",
            "elevated",
            0.96,
            False,
            ["get_support_resources", "search_knowledge"],
            "测试",
        ),
        ("counseling", "urgent", 0.96, False, ["crisis_support"], "测试"),
        ("chat", "urgent", 0.96, False, ["crisis_support"], "测试"),
        ("counseling", "low", 0.55, False, ["get_support_resources", "search_knowledge"], "测试"),
        ("counseling", "low", 0.96, True, ["get_support_resources", "search_knowledge"], "测试"),
        (
            "chat",
            "low",
            0.96,
            False,
            ["get_support_resources"],
            "我想寻找专业心理咨询的支持资源，可以给我已有的求助渠道吗？",
        ),
        ("chat", "low", 0.96, False, [], "我暂时不需要心理咨询的支持资源，只想聊聊。"),
        ("chat", "urgent", 0.96, False, ["crisis_support"], "我需要心理咨询的支持资源。"),
    ],
)
def test_local_router_and_real_graph(kind, band, probability, professional, expected, message):
    async def run():
        settings = Settings(router_provider="laya")
        runtime = AgentGraphRuntime.__new__(AgentGraphRuntime)
        runtime.settings = settings
        runtime.planner = AgentPlanner(settings=settings)
        calls = []

        def respond(request):
            payload = json.loads(request.content)
            assert payload["contractVersion"] == ROUTER_CONTRACT
            assert "username" not in payload["state"]
            choices = {
                "route": "chat" if kind == "chat" else "knowledge",
                "conversation_kind": kind,
                "risk_band": band,
                "support_need": "professional" if professional else "general",
            }
            return httpx.Response(
                200,
                json={
                    "contractVersion": ROUTER_CONTRACT,
                    "model": "local-test",
                    "answers": {
                        task: answer(
                            label,
                            ROUTER_QUESTIONS[task]["criteria"],
                            probability if task == "risk_band" else 0.96,
                        )
                        for task, label in choices.items()
                    },
                },
            )

        await runtime.planner.router.client.aclose()
        runtime.planner.router.client = httpx.AsyncClient(
            base_url="http://local-router", transport=httpx.MockTransport(respond)
        )

        async def call(name, arguments):
            calls.append(name)
            return {
                "risk_scan": {"highRisk": False},
                "get_user_context": {"username": "alice", "sessionId": 1},
                "search_knowledge": {"hits": [], "metrics": {}},
                "get_support_resources": [],
                "crisis_support": {"reply": "固定支持", "resources": []},
                "persist_turn": {"saved": True},
            }[name]

        runtime.tools = AsyncMock()
        runtime.tools.call.side_effect = call
        runtime._respond = AsyncMock(return_value={"reply": "支持回复"})
        runtime.graph = runtime._build_graph().compile()
        try:
            result = await runtime.invoke(
                AgentRequest(username="alice", sessionId=1, message=message, requestId="local-test")
            )
            assert calls[2:-1] == expected
            assert result.crisis == (band == "urgent")
            if result.crisis:
                runtime._respond.assert_not_called()
        finally:
            await runtime.planner.close()

    asyncio.run(run())


def test_executor_observes_results_and_cannot_escalate_permissions():
    async def run():
        controller = ToolController(Settings(executor_provider="laya"))
        seen = []

        async def evaluate(state, tasks, **kwargs):
            seen.append(state)
            action = "search_knowledge" if not state["observations"] else "finish"
            return {"decisions": {"next_action": {"choice": action, "accepted": True}}}

        controller.model.evaluate = evaluate
        invoke = AsyncMock(return_value={"hits": ["evidence"]})
        try:
            results, trace = await controller.run(
                plan_for_route("knowledge", "问题", "test"), "问题", invoke, lambda *_: None
            )
            assert list(results) == ["search_knowledge"]
            assert len(trace) == 2
            assert "search_knowledge" in seen[1]["observations"]
            assert "get_dashboard" not in seen[0]["allowedTools"]
            controller.model.evaluate = AsyncMock(
                return_value={
                    "decisions": {"next_action": {"choice": "persist_turn", "accepted": True}}
                }
            )
            with pytest.raises(ValueError):
                await controller.run(
                    plan_for_route("knowledge", "问题", "test"), "问题", invoke, lambda *_: None
                )
        finally:
            await controller.close()

    asyncio.run(run())


def test_required_resource_survives_executor_finish_and_budget_is_bounded():
    async def run():
        controller = ToolController(Settings(executor_provider="laya"))
        controller.model.evaluate = AsyncMock(
            return_value={"decisions": {"next_action": {"choice": "finish", "accepted": True}}}
        )
        plan = plan_for_route("knowledge", "问题", "test")
        plan.allowed_tools.append("get_support_resources")
        plan.required_tools = ["get_support_resources"]
        invoke = AsyncMock(return_value=[])
        try:
            results, trace = await controller.run(plan, "问题", invoke, lambda *_: None)
            assert list(results) == ["get_support_resources"]
            assert trace[-1]["action"] == "finish"
            assert invoke.await_count == 1
        finally:
            await controller.close()

    asyncio.run(run())


def test_memory_is_scoped_idempotent_and_preserves_roles(tmp_path):
    store = MemoryStore(str(tmp_path / "memory.sqlite3"))
    job = {
        "username": "alice",
        "sessionId": 1,
        "requestId": "turn-1",
        "userMessage": "我喜欢安静散步",
        "assistantReply": "建议你每天跑步",
        "createdAt": "2026-10-09T12:00:00",
    }
    store.ingest(job)
    store.ingest(job)
    assert len(store.candidates("alice", "散步")) == 1
    assert store.candidates("bob", "散步") == []
    assert all(item["role"] == "user" for item in store.candidates("alice", "跑步"))
    with store.connect() as db:
        assert db.execute("SELECT count(*) FROM observations").fetchone()[0] == 2
    store.forget("alice")
    assert store.candidates("alice", "散步") == []


def test_memory_model_has_independent_contract_and_failures_preserve_evidence(tmp_path):
    async def run():
        service = MemoryService(
            Settings(memory_provider="laya", memory_store_path=str(tmp_path / "index.sqlite3"))
        )
        service.controller.evaluate = AsyncMock(side_effect=httpx.ConnectError("offline"))
        try:
            assert service.controller.contract != ROUTER_CONTRACT
            await service.ingest(
                {
                    "username": "alice",
                    "sessionId": 1,
                    "requestId": "x",
                    "userMessage": "喜欢散步",
                    "assistantReply": "收到",
                    "createdAt": "2026-10-09T12:00:00",
                }
            )
            result = await service.retrieve("alice", "散步")
            assert result["status"] == "lexical_fallback"
            assert result["evidence"][0]["content"] == "喜欢散步"
            assert result["rounds"] <= 2
        finally:
            await service.close()

    asyncio.run(run())


def test_decision_profiles_cannot_accept_each_others_contract(monkeypatch):
    from fastapi.testclient import TestClient

    from psych_agent_runtime.routing_service import create_app

    monkeypatch.setenv("DECISION_PROFILE", "memory")
    monkeypatch.delenv("DECISION_CHECKPOINT", raising=False)
    with TestClient(create_app()) as client:
        assert not client.get("/health").json()["ready"]
        assert (
            client.post(
                "/v1/decisions",
                json={
                    "contractVersion": ROUTER_CONTRACT,
                    "state": {"message": "测试"},
                    "tasks": ["route"],
                },
            ).status_code
            == 409
        )
