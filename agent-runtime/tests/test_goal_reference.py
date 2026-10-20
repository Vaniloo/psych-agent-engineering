from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from psych_agent_runtime.config import Settings
from psych_agent_runtime.goal_operations import GoalOperations
from psych_agent_runtime.goal_progress import eligible_report, select_progress
from psych_agent_runtime.goal_reference import followup_outcome
from psych_agent_runtime.graph import AgentGraphRuntime
from psych_agent_runtime.route_policy import guarantee_requested_reads, plan_for_route

TITLE = "先确定一个最容易调整的饮食点，比如每天多吃一份蔬菜或减少一次含糖饮料。"
GOALS = [{"id": 7, "version": 0, "status": "active", "title": "健康饮食", "actions": [
    {"id": "choose", "title": TITLE, "status": "planned"},
    {"id": "meal", "title": "按这个调整点选择食物，并记下吃了什么。", "status": "planned"},
]}]
CONTEXT = {"messages": [
    {"role": "user", "content": "第一个小步骤是什么"},
    {"role": "assistant", "content": "第一个小步骤是：" + TITLE.replace("或减少", "，或者减少")},
]}


@pytest.mark.asyncio
@pytest.mark.parametrize("text,outcome", [
    ("OK，那我今天完成了", "done"), ("这一步我做完了。", "done"),
    ("我今天做了一点", "partial"),
])
async def test_current_user_status_binds_unique_adjacent_owned_step_without_fabricating_quote(text, outcome):
    model = AsyncMock()
    result = await select_progress(text, GOALS, model, CONTEXT)
    assert eligible_report(text)
    assert result == {"goalId": 7, "version": 0, "goalTitle": "健康饮食",
                      "actionId": "choose", "actionTitle": TITLE, "outcome": outcome, "quote": text}
    model.evaluate.assert_not_awaited()


@pytest.mark.parametrize("text", [
    "我今天还没完成", "明天我就完成了", "她今天完成了", "我今天完成了，不用同步",
    "我今天完成了？", "我今天完成了吧", "假如我今天完成了", "他说我今天完成了",
])
def test_uncertainty_future_third_party_and_optout_do_not_supply_short_status(text):
    assert followup_outcome(text) is None


@pytest.mark.asyncio
async def test_no_reference_multi_step_reply_duplicates_and_unrelated_exchange_abstain():
    text = "OK，那我今天完成了"
    contexts = [None, {"messages": []}, {"messages": [
        {"role": "user", "content": "今天天气怎样"}, CONTEXT["messages"][-1],
    ]}, {"messages": [CONTEXT["messages"][0], {"role": "assistant", "content":
        TITLE + GOALS[0]["actions"][1]["title"]}]}, {"messages": [
        *CONTEXT["messages"], {"role": "user", "content": "换个话题"},
        {"role": "assistant", "content": "好"},
    ]}]
    for context in contexts:
        assert await select_progress(text, GOALS, context=context) is None
    assert await select_progress(text, GOALS * 2, context=CONTEXT) is None
    assert await select_progress(text, GOALS * 9, context=CONTEXT) is None
    wrong_position = {"messages": [
        CONTEXT["messages"][0], {"role": "assistant", "content": GOALS[0]["actions"][1]["title"]},
    ]}
    assert await select_progress(text, GOALS, context=wrong_position) is None


def test_step_question_reads_owned_goals_even_when_router_calls_it_chat():
    plan = guarantee_requested_reads(plan_for_route("chat", "", "test"), "第一个小步骤是什么")
    assert plan.required_tools == ["get_goal_context"]


@pytest.mark.asyncio
async def test_loop_opens_goal_read_for_short_status_even_when_router_says_chat():
    runtime = AgentGraphRuntime.__new__(AgentGraphRuntime)
    runtime.settings = Settings(memory_context_recall_enabled=False)
    runtime._emit = lambda *_: None
    state = {"message": "OK，那我今天完成了", "username": "alice", "session_id": 1,
             "context": {**CONTEXT, "username": "alice", "sessionId": 1},
             "plan": plan_for_route("chat", "", "test").model_dump()}
    initialized = await runtime._loop_initialize(state)
    assert "get_goal_context" in initialized["plan"]["required_tools"]
    tools = SimpleNamespace(call=AsyncMock(return_value={"applied": True, "checkIn": {"id": 8}}))
    result = await GoalOperations(tools, None, runtime._emit).sync_progress({
        **state, "turn_acceptance": {"accepted": True},
        "tool_results": {"get_goal_context": {"goals": GOALS}},
        "request_id": "original-short-status",
    })
    assert result["goal_updates"] == [{"id": 8}]
    assert tools.call.call_args.args[1]["quote"] == state["message"]


@pytest.mark.asyncio
@pytest.mark.parametrize("text,index,outcome", [
    ("OK，小步骤1我做完了", 0, "done"), ("第一步我完成了", 0, "done"),
    ("步骤 ２ 我搞定了", 1, "done"), ("我完成第2步了", 1, "done"),
    ("小步骤1我做了一部分", 0, "partial"),
])
async def test_numbered_status_uses_owned_conversation_reference_not_assistant_wording(text, index, outcome):
    # Many goals may exist; the ID is read from authoritative command metadata.
    goals = [*GOALS, {**GOALS[0], "id": 99}]
    assert eligible_report(text)
    result = await select_progress(text, goals, context={"messages": []}, conversation_goal_id=7)
    assert result["goalId"] == 7 and result["actionId"] == GOALS[0]["actions"][index]["id"]
    assert result["quote"] == text and result["outcome"] == outcome
    assert await select_progress(text, goals) is None


@pytest.mark.asyncio
@pytest.mark.parametrize("text", [
    "小步骤1我还没做完", "小步骤1明天我做完", "小步骤1她做完了", "小步骤1我做完了吗？",
    "小步骤1我做完了，不要更新", "如果小步骤1我做完了", "步骤1和步骤2都完成了",
    "步骤0完成了", "步骤13完成了",
])
async def test_number_reference_does_not_authorize_uncertain_unsupported_or_multiple_status(text):
    assert await select_progress(text, GOALS, context=CONTEXT, conversation_goal_id=7) is None


@pytest.mark.asyncio
async def test_numbered_completion_cannot_select_done_paused_missing_or_out_of_range_action():
    for goals, goal_id in [(GOALS, 99), ([{**GOALS[0], "status": "paused"}], 7),
                           ([{**GOALS[0], "actions": [{**GOALS[0]["actions"][0], "status": "done"}]}], 7)]:
        assert await select_progress("小步骤1我完成了", goals, conversation_goal_id=goal_id) is None
    assert await select_progress("小步骤3我完成了", GOALS, conversation_goal_id=7) is None
