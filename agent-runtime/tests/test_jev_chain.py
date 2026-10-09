import asyncio
import json
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock

import httpx
import pytest
from pydantic import ValidationError

from psych_agent_runtime.capabilities import normalize_result, validate_arguments
from psych_agent_runtime.config import Settings
from psych_agent_runtime.graph import AgentGraphRuntime
from psych_agent_runtime.jev_decisions import CONVERSATION_KINDS, RISK_BANDS
from psych_agent_runtime.mcp_client import McpToolClient
from psych_agent_runtime.planner import AgentPlanner
from psych_agent_runtime.routing_contract import ROUTES
from psych_agent_runtime.schemas import AgentRequest
from psych_agent_runtime.telemetry import RunTelemetry


def choice(label, labels, probability=0.96):
    return {
        "type": "choice",
        "choice": label,
        "probabilities": {
            name: probability if name == label else (1 - probability) / (len(labels) - 1)
            for name in labels
        },
    }


@pytest.mark.parametrize(
    "kind,band,risk_probability,expected_tools,crisis,professional_probability",
    [
        ("chat", "low", 0.96, [], False, 0.1),
        ("counseling", "low", 0.96, ["search_knowledge"], False, 0.1),
        ("counseling", "elevated", 0.96, ["search_knowledge", "get_support_resources"], False, 0.1),
        ("counseling", "urgent", 0.96, ["crisis_support"], True, 0.1),
        ("counseling", "urgent", 0.96, ["crisis_support"], True, 0.5),
        ("counseling", "low", 0.96, ["search_knowledge", "get_support_resources"], False, 0.5),
        ("counseling", "low", 0.96, ["search_knowledge", "get_support_resources"], False, 0.95),
        ("counseling", "low", 0.55, ["search_knowledge", "get_support_resources"], False, 0.1),
    ],
)
def test_native_jev_hierarchy_controls_real_compiled_graph(
    kind, band, risk_probability, expected_tools, crisis, professional_probability
):
    async def run():
        settings = Settings(router_provider="jev", jev_api_key="test-key")
        runtime = AgentGraphRuntime.__new__(AgentGraphRuntime)
        runtime.settings = settings
        runtime.planner = AgentPlanner(AsyncMock(), settings)
        requests, tools = [], []

        def respond(request):
            body = json.loads(request.content)
            assert request.url.path == "/v1/systemone"
            assert request.headers["Authorization"] == "Bearer test-key"
            assert "username" not in body["state"]
            assert body["model"] == "jev-1.13.0"
            requests.append(body)
            if "conversation_kind" in body["questions"]:
                answers = {
                    "route": choice("chat" if kind == "chat" else "knowledge", ROUTES),
                    "conversation_kind": choice(kind, CONVERSATION_KINDS),
                }
            else:
                answers = {
                    "risk_band": choice(band, RISK_BANDS, risk_probability),
                    "needs_professional_support": {
                        "type": "noul",
                        "noul": professional_probability,
                    },
                }
            return httpx.Response(
                200,
                json={
                    "model": "jev-1.13.0",
                    "answers": answers,
                    "usage": {"input_tokens": 100, "output_tokens": 0},
                },
            )

        await runtime.planner.router.client.aclose()
        runtime.planner.router.client = httpx.AsyncClient(
            base_url="https://api.typesafe.ai",
            headers={"Authorization": "Bearer test-key"},
            transport=httpx.MockTransport(respond),
        )

        async def call(name, arguments):
            tools.append(name)
            return {
                "risk_scan": {"highRisk": False},
                "get_user_context": {"username": "alice", "sessionId": 1},
                "search_knowledge": {"hits": [], "metrics": {}},
                "get_support_resources": [
                    {
                        "name": "测试专业支持",
                        "type": "phone",
                        "contact": "test",
                        "description": "测试资源",
                        "urgent": False,
                    }
                ],
                "crisis_support": {"reply": "固定危机支持", "resources": []},
                "persist_turn": {"saved": True},
            }[name]

        runtime.tools = AsyncMock()
        runtime.tools.call.side_effect = call
        runtime._respond = AsyncMock(return_value={"reply": "支持性回复"})
        runtime._compress_memory = AsyncMock(return_value={"compression": {}})
        runtime.graph = runtime._build_graph().compile()
        try:
            result = await runtime.invoke(
                AgentRequest(
                    username="alice", sessionId=1, message="测试请求", requestId="jev-test"
                )
            )
            assert tools[2:-1] == expected_tools
            assert len(requests) == (1 if kind == "chat" else 2)
            assert result.crisis is crisis
            if crisis:
                runtime._respond.assert_not_called()
            if "get_support_resources" in expected_tools:
                assert result.resources[0].name == "测试专业支持"
            assert result.metrics.cost_mode == "hybrid"
            assert result.metrics.actual_cost_usd > 0
        finally:
            await runtime.planner.close()

    asyncio.run(run())


def test_capability_schema_rejects_unknown_fields_and_out_of_range_limits():
    with pytest.raises(ValidationError):
        validate_arguments("search_knowledge", {"query": "睡眠", "username": "forged"})
    with pytest.raises(ValidationError):
        validate_arguments("search_knowledge", {"query": "睡眠", "limit": 1000})
    with pytest.raises(ValueError):
        validate_arguments("execute_shell", {})
    with pytest.raises(ValueError):
        normalize_result("persist_turn", {"saved": False})
    with pytest.raises(ValueError):
        normalize_result("risk_scan", {"highRisk": "false"})


@pytest.mark.parametrize(
    "name,args,replayed",
    [
        ("get_dashboard", {"username": "alice"}, True),
        (
            "persist_turn",
            {
                "username": "alice",
                "session_id": 1,
                "user_message": "你好",
                "assistant_reply": "你好",
            },
            False,
        ),
        ("crisis_support", {"username": "alice", "message": "测试"}, False),
    ],
)
def test_ambiguous_transport_failure_does_not_replay_writes(monkeypatch, name, args, replayed):
    import mcp
    import mcp.client.streamable_http

    @asynccontextmanager
    async def transport(*_a, **_k):
        yield None, None, None

    class Session:
        def __init__(self, *_):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            pass

        async def initialize(self):
            pass

        async def call_tool(self, *_a, **_k):
            raise ConnectionError("reply lost after dispatch")

    monkeypatch.setattr(mcp.client.streamable_http, "streamable_http_client", transport)
    monkeypatch.setattr(mcp, "ClientSession", Session)

    async def run():
        client = McpToolClient(Settings(), AsyncMock(), AsyncMock())
        client._fallback = AsyncMock(return_value={"reports": []})
        if replayed:
            assert await client.call(name, args) == {"reports": []}
            client._fallback.assert_awaited_once()
        else:
            with pytest.raises(ConnectionError):
                await client.call(name, args)
            client._fallback.assert_not_called()

    asyncio.run(run())


def test_jev_cost_is_separate_from_local_generation_cost():
    telemetry = RunTelemetry("cost-test", Settings())
    telemetry.record_decision_call("jev-1.13.0", {"input_tokens": 1000}, 20)
    metrics = telemetry.snapshot()
    assert metrics["actualCostUsd"] == 0.000042
    assert metrics["costMode"] == "hybrid"
