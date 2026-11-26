import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from benchmarks.runtime_fixture import make_runtime

from psych_agent_runtime.config import Settings
from psych_agent_runtime.planner import AgentPlanner
from psych_agent_runtime.route_policy import plan_for_route
from psych_agent_runtime.schemas import AgentRequest






@pytest.mark.asyncio
@pytest.mark.parametrize("message, route, tools", [
    ("帮我制定计划", "strategy", ["search_knowledge"]),
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
        runtime.decision_client.chat_json.assert_not_awaited()
        assert calls == ["risk_scan", "crisis_support", "persist_turn"]
        high_risk = False
        second = await runtime.invoke(request.model_copy(update={"request_id": "next-test"}))
        assert not second.crisis
        assert second.reply == "普通回复"
        assert second.resources == []
        runtime.planner.plan.assert_called_once()

    asyncio.run(run())




@pytest.mark.asyncio
@pytest.mark.parametrize("planner_reply,risk_reply,planner_provider,level", [
    (TimeoutError(), {"risk_band": "urgent", "support_need": "professional"}, "rules", "crisis"),
    ({"route": "chat", "conversation_kind": "counseling"}, TimeoutError(), "deepseek", "professional_support"),
    ({"route": "persist_turn"}, {"risk_band": "urgent", "support_need": []}, "rules", "crisis"),
    ({"route": "chat", "conversation_kind": "counseling"}, {"risk_band": "low", "support_need": "general"}, "deepseek", "self_help"),
])
async def test_independent_planner_and_risk_calls(planner_reply, risk_reply, planner_provider, level):
    from psych_agent_runtime.schemas import AgentContext

    runtime = make_runtime()
    runtime._emit = lambda *_: None
    client = runtime.decision_client
    client.chat_json.side_effect = [planner_reply, risk_reply]
    runtime.planner = AgentPlanner(client)
    context = AgentContext(username="private-account", sessionId=42, rolePrompt="private-role-prompt",
        messages=[{"role": "user", "content": "最近遇到了一些事。"}])
    state = {"message": "我想和你聊聊。", "context": context.model_dump(by_alias=True)}
    state.update(await runtime._planner(state))
    result = await runtime._risk_decision(state)
    first, second = client.chat_json.await_args_list
    assert [first.kwargs["purpose"], second.kwargs["purpose"]] == ["planner_intent", "risk_review"]
    assert '"route"' in first.args[0][0]["content"] and '"risk_band"' not in first.args[0][0]["content"]
    assert '"risk_band"' in second.args[0][0]["content"] and '"route"' not in second.args[0][0]["content"]
    for call in (first, second):
        assert "最近遇到了一些事" in call.args[0][1]["content"]
        assert "private-account" not in str(call) and "private-role-prompt" not in str(call)
    assert state["plan"]["routing"]["provider"] == planner_provider
    assert result["support_level"] == level
    assert "persist_turn" not in state["plan"]["allowed_tools"]


@pytest.mark.asyncio
async def test_valid_model_route_never_runs_keyword_planner(monkeypatch):
    client = SimpleNamespace(model="fixture", chat_json=AsyncMock(return_value={
        "route": "strategy", "conversation_kind": "counseling"}))
    planner = AgentPlanner(client)
    monkeypatch.setattr(planner, "_deterministic_plan", lambda *_: pytest.fail("unexpected fallback"))
    # A recall is explicitly requested alongside advice. Keep both the model's route and the read.
    plan = await planner.plan("回顾我之前提到的偏好，帮我制定下一步练习计划。")
    assert plan.intent == plan.routing["effectiveRoute"] == "strategy"
    assert plan.routing["provider"] == "deepseek"
    assert "memory_search" in plan.required_tools
