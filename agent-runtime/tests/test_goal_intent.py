import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from psych_agent_runtime.config import Settings
from psych_agent_runtime.goal_creation import explicit_creation
from psych_agent_runtime.goal_intent import resolve_goal_intent
from psych_agent_runtime.graph import AgentGraphRuntime
from psych_agent_runtime.planner import AgentPlanner
from psych_agent_runtime.schemas import AgentContext


def history():
    return AgentContext(username="alice", sessionId=7, messages=[
        {"role": "user", "content": "我想每天睡够八小时，先聊聊，不要保存。"},
        {"role": "assistant", "content": "可以把每天睡够八小时作为一个目标。"},
    ])


@pytest.mark.asyncio
@pytest.mark.parametrize("text", ["看一下我完成了什么目标", "我已经达成哪些目标了？",
                                 "盘点一下完成的目标", "看看我有哪些目标"])
async def test_general_queries_read_list_without_old_named_focus(text):
    c = history()
    c.messages[0].content = "我想聊聊目标「旧目标」的进展"
    planner = AgentPlanner(settings=Settings(router_provider="qwen"))
    planner.router.decide = AsyncMock(return_value={"accepted": True, "route": "chat"})
    try:
        plan = await planner.plan(text, c)
        assert "get_goal_context" in plan.required_tools
        assert "goalFocus" not in plan.routing
        if "完成" in text or "达成" in text:
            assert plan.routing["goalStatusFilter"] == "completed"
    finally:
        await planner.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("text", ["那把这个设成新目标吧", "将它加入我的目标", "这个就定为接下来要做的目标"])
async def test_paraphrase_uses_bounded_context_and_preserves_current_authorization(text):
    assert explicit_creation(text)
    llm = SimpleNamespace(chat_json=AsyncMock(return_value={
        "intent": "create", "goalText": "每天睡够八小时", "question": ""}))
    result = await resolve_goal_intent(text, history(), llm)
    assert result["goalText"] == "每天睡够八小时"
    call = llm.chat_json.call_args
    data = json.loads(call.args[0][1]["content"])
    assert data["currentUserCommand"] == text
    assert data["recentConversation"][0]["role"] == "user"
    assert "不要保存" in data["recentConversation"][0]["content"]


@pytest.mark.asyncio
async def test_ambiguous_or_failed_resolution_never_calls_creation_tool():
    runtime = AgentGraphRuntime.__new__(AgentGraphRuntime)
    runtime.tools = SimpleNamespace(call=AsyncMock())
    runtime._emit = lambda *_: None
    base = {"username": "alice", "session_id": 7, "request_id": "ref",
            "message": "那把这个设成新目标吧", "persistence_status": "saved"}
    for intent in ["none", "clarify"]:
        state = {**base, "goal_intent": {"intent": intent, "question": "睡眠还是运动？"}}
        assert await runtime._goal_creation(state) == {"created_goals": []}
    runtime.tools.call.assert_not_awaited()
    llm = SimpleNamespace(chat_json=AsyncMock(side_effect=RuntimeError("unavailable")))
    result = await resolve_goal_intent(base["message"], history(), llm)
    assert result["intent"] == "clarify" and result["unavailable"]


@pytest.mark.asyncio
async def test_only_normalized_goal_goes_to_local_decision_original_turn_goes_to_write():
    runtime = AgentGraphRuntime.__new__(AgentGraphRuntime)
    model = SimpleNamespace(evaluate=AsyncMock(return_value={
        "decisions": {"goal_creation": {"accepted": True, "choice": "create"}}}))
    runtime._goal_decision_model = lambda: model
    runtime.tools = SimpleNamespace(call=AsyncMock(return_value={"created": True, "goal": {"id": 9}}))
    runtime._emit = lambda *_: None
    state = {"username": "alice", "session_id": 7, "request_id": "ref",
             "message": "那把这个设成新目标吧", "persistence_status": "saved",
             "goal_intent": {"intent": "create", "goalText": "每天睡够八小时"}}
    assert (await runtime._goal_creation(state))["created_goals"] == [{"id": 9}]
    assert model.evaluate.call_args.args[0]["userExpression"] == "请创建一个目标：每天睡够八小时"
    runtime.tools.call.assert_awaited_once_with("create_goal", {
        "username": "alice", "session_id": 7, "request_id": "ref"})


@pytest.mark.asyncio
async def test_clear_command_and_negative_quote_wish_do_not_add_semantic_request():
    llm = SimpleNamespace(chat_json=AsyncMock())
    for message in ["帮我创建一个睡眠目标", "不要把这个设成新目标", "他说把这个设成新目标", "我想睡够八小时"]:
        assert await resolve_goal_intent(message, history(), llm) == {}
    llm.chat_json.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("verb", ["设置", "设定", "制定"])
async def test_explicit_synonym_normalizes_content_but_preserves_original_command(verb):
    message = verb + "一个每天睡够八小时的目标"
    client = SimpleNamespace(chat_json=AsyncMock(return_value={
        "intent": "create", "goalText": "每天睡够八小时", "question": "",
    }))
    result = await resolve_goal_intent(message, history(), client)
    assert result["intent"] == "create" and result["goalText"] == "每天睡够八小时"
    payload = json.loads(client.chat_json.call_args.args[0][1]["content"])
    assert payload["currentUserCommand"] == message
