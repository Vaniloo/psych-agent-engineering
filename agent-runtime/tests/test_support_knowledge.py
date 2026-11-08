from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from benchmarks.loop_probe import run_controller, run_read_loop
from benchmarks.runtime_fixture import make_runtime

from psych_agent_runtime.config import Settings
from psych_agent_runtime.context_recall import with_context_recall
from psych_agent_runtime.route_policy import guarantee_requested_reads, plan_for_route
from psych_agent_runtime.schemas import AgentContext, Plan
from psych_agent_runtime.support_knowledge import with_support_knowledge
from psych_agent_runtime.tool_controller import ToolController


@pytest.mark.parametrize(
    "message",
    [
        "我最近特别焦虑",
        "我感觉我心理特别焦虑",
        "最近焦虑得厉害",
        "我今天压力很大",
        "我最近睡不着",
        "我最近情绪很低落",
        "这几天脑子总是停不下来，老担心事情会出错，我该怎么办？",
        "我最近老是担心事情出错。",
    ],
)
def test_current_distress_has_required_read_without_turning_chat_into_lecture(message):
    plan = with_support_knowledge(plan_for_route("chat", message, "test"), message, "ordinary")
    assert plan.intent == "chat"
    assert plan.required_tools == ["search_knowledge"]
    assert "我" not in plan.routing["supportKnowledge"]["query"].replace("自我", "")
    assert plan.max_tool_calls <= 2


@pytest.mark.parametrize(
    "message",
    [
        "我最近特别焦虑，只想有人听我说，不用建议",
        "我没有焦虑，今天挺好",
        "我朋友最近特别焦虑",
        "例如我最近特别焦虑",
        "我想聊目标进度",
        "你记得我以前焦虑时怎么处理的吗？",
        "我没有一直担心出错，今天挺放松的。",
        "我朋友脑子总是停不下来，老担心事情出错。",
        "我最近脑子停不下来，只想有人听我说，不用建议。",
    ],
)
def test_listening_denial_third_party_and_factual_reads_do_not_force_knowledge(message):
    plan = with_support_knowledge(plan_for_route("chat", message, "test"), message, "ordinary")
    assert "search_knowledge" not in plan.required_tools


def test_listening_preference_persists_and_explicit_help_request_reopens_read():
    context = {
        "messages": [
            {"role": "user", "content": "我现在只想倾诉，先别给建议"},
            {"role": "assistant", "content": "我们可以找方法"},
        ]
    }
    plan = with_support_knowledge(
        plan_for_route("knowledge", "", "test"), "我最近特别焦虑", "self_help", context
    )
    assert "search_knowledge" not in plan.allowed_tools and plan.intent == "chat"
    for message in ["我现在特别焦虑，怎么缓解？", "我现在特别焦虑，给我一个缓解的方法吧。"]:
        plan = with_support_knowledge(
            plan_for_route("chat", "", "test"), message, "self_help", context
        )
        assert plan.required_tools == ["search_knowledge"]


def test_misrouted_feeling_is_still_companionship_with_fresh_sources():
    for route in ["knowledge", "memory", "clarify"]:
        plan = with_support_knowledge(
            plan_for_route(route, "", "test"), "我最近特别焦虑", "ordinary"
        )
        assert plan.intent == "chat" and "search_knowledge" in plan.required_tools


def test_declining_advice_keeps_explicit_definition_read_but_no_lookup_is_honored():
    message = "什么是焦虑？不用给建议，只讲含义。"
    plan = guarantee_requested_reads(plan_for_route("chat", "", "test"), message)
    assert with_support_knowledge(plan, message, "ordinary").required_tools == ["search_knowledge"]
    plan = with_support_knowledge(
        plan_for_route("knowledge", "", "test"), "我最近焦虑，但不要查心理知识库。", "self_help"
    )
    assert "search_knowledge" not in plan.allowed_tools


def test_knowledge_and_background_memory_share_existing_budget_and_crisis_is_untouched():
    message = "我最近特别焦虑"
    plan = with_support_knowledge(plan_for_route("chat", message, "test"), message, "ordinary")
    plan = with_context_recall(plan, message, AgentContext(username="alice", sessionId=1))
    assert plan.required_tools == ["search_knowledge", "memory_search"] and plan.max_tool_calls == 2
    crisis = plan_for_route("chat", message, "test")
    assert with_support_knowledge(crisis, message, "crisis").required_tools == []
    full = plan_for_route("strategy", message, "test")
    full.required_tools = ["get_goal_context", "memory_search"]
    full = with_support_knowledge(full, message, "self_help")
    assert len(full.required_tools) == 2 and full.routing["supportKnowledge"]["mode"] == "deferred"


@pytest.mark.asyncio
@pytest.mark.parametrize("professional", [False, True])
async def test_bad_chat_route_and_executor_finish_cannot_skip_distress_grounding(professional):
    runtime = make_runtime()
    runtime._emit = lambda *_: None
    runtime.settings = Settings()
    decision = {"riskBand": "low", "accepted": True, "needsProfessionalSupport": professional}
    plan = plan_for_route(
        "chat", "", "test", {"conversationKind": "chat", "riskDecision": decision}
    )
    state = {
        "message": "我最近特别焦虑",
        "plan": plan.model_dump(),
        "context": {"username": "alice", "sessionId": 1, "messages": []},
    }
    result = await runtime._risk_decision(state)
    updated = Plan.model_validate(result["plan"])
    assert updated.required_tools == (
        ["get_support_resources", "search_knowledge"] if professional else ["search_knowledge"]
    )
    assert result["risk_decision"]["riskBand"] == "low"
    controller = ToolController(Settings(executor_provider="rules"))
    controller.model = SimpleNamespace(
        evaluate=AsyncMock(
            return_value={"decisions": {"next_action": {"choice": "finish", "accepted": True}}}
        )
    )
    invoke = AsyncMock(return_value={"hits": []})
    await run_controller(controller, updated, state["message"], invoke, lambda *_: None)
    assert "search_knowledge" in [call.args[0] for call in invoke.call_args_list]
    assert invoke.await_count <= 2


@pytest.mark.asyncio
async def test_search_uses_general_topic_query_and_three_sources():
    runtime = make_runtime()
    runtime.settings = Settings(memory_context_recall_enabled=False)
    runtime._emit = lambda *_: None
    runtime.tools = SimpleNamespace(catalog=SimpleNamespace(refresh=AsyncMock()), call=AsyncMock(return_value={"hits": [], "metrics": {}}))
    plan = with_support_knowledge(plan_for_route("chat", "", "test"), "我最近特别焦虑", "ordinary")
    await run_read_loop(
        runtime,
        {
            "username": "alice",
            "session_id": 1,
            "message": "我最近特别焦虑",
            "plan": plan.model_dump(),
        },
    )
    runtime.tools.call.assert_awaited_once_with(
        "search_knowledge", {"query": "面对焦虑困扰，有哪些自我照顾和放松方法？", "limit": 3}
    )
