from unittest.mock import AsyncMock

import pytest

from psych_agent_runtime.goal_progress import eligible_report, select_progress
from psych_agent_runtime.graph import AgentGraphRuntime

GOALS = [
    {
        "id": 7,
        "version": 2,
        "title": "给忙碌留一点休息",
        "status": "active",
        "actions": [
            {"id": "walk", "title": "午后离开桌前，走一走", "status": "planned"},
            {"id": "water", "title": "喝一杯水", "status": "done"},
        ],
    }
]


def test_non_action_chat_does_not_trigger_goal_matching():
    assert not eligible_report("今天事情忙完了，想轻松聊两句。")
    assert not eligible_report("我今天心情好多了。")
    assert eligible_report("我今天出去散步了一点，还没完成五分钟。")
    assert not eligible_report("我已经出去散步五分钟了，不用更新进度。")


@pytest.mark.asyncio
async def test_rules_accept_only_one_actual_exact_owned_action():
    result = await select_progress("我今天午后离开桌前，走了走。", GOALS)
    assert result["goalId"] == 7 and result["actionId"] == "walk" and result["outcome"] == "done"
    for text in [
        "我想明天午后离开桌前，走一走。",
        "我今天还没有午后离开桌前，走一走。",
        "朋友已经午后离开桌前，走一走了。",
        "建议我已经午后离开桌前，走一走了。",
    ]:
        assert await select_progress(text, GOALS) is None
    assert await select_progress("我已经喝一杯水了。", GOALS) is None
    assert await select_progress("我今天午后离开桌前，走了走。", GOALS * 2) is None
    assert (
        await select_progress("我今天午后离开桌前，走了走。", [{**GOALS[0], "status": "paused"}])
        is None
    )


@pytest.mark.asyncio
async def test_executor_choices_are_mapped_to_owned_candidates_and_abstain():
    model = AsyncMock()
    decisions = {
        "goal_action": {"accepted": True, "choice": "action_0"},
        "goal_outcome": {"accepted": True, "choice": "partial"},
    }
    model.evaluate.return_value = {"decisions": decisions}
    result = await select_progress("我今天出去走了一点，还想慢慢继续。", GOALS, model)
    assert (
        result["actionId"] == "walk" and result["version"] == 2 and result["outcome"] == "partial"
    )
    assert "username" not in model.evaluate.call_args.args[0]
    assert set(model.evaluate.call_args.kwargs["labels_by_task"]["goal_action"]) == {
        "none",
        "action_0",
    }
    decisions["goal_action"]["accepted"] = False
    assert await select_progress("我今天出去走了一点。", GOALS, model) is None
    decisions["goal_action"] = {"accepted": True, "choice": "action_99"}
    assert await select_progress("我今天出去走了一点。", GOALS, model) is None
    model.reset_mock()
    assert await select_progress("x" * 501, GOALS, model) is None
    model.evaluate.assert_not_called()


@pytest.mark.asyncio
async def test_progress_is_after_persistence_skips_crisis_and_respects_preference():
    runtime = AgentGraphRuntime.__new__(AgentGraphRuntime)
    runtime.tools = AsyncMock()
    runtime.tool_controller = None
    runtime._emit = lambda *args: None
    state = {
        "username": "alice",
        "session_id": 1,
        "request_id": "turn",
        "message": "我今天午后离开桌前，走了走。",
        "persistence_status": "failed",
    }
    assert await runtime._goal_progress(state) == {"goal_updates": []}
    runtime.tools.call.assert_not_called()
    state.update(persistence_status="saved", crisis=True)
    assert await runtime._goal_progress(state) == {"goal_updates": []}
    runtime.tools.call.assert_not_called()
    state["crisis"] = False
    runtime.tools.call.return_value = {"autoSync": False, "goals": GOALS}
    assert await runtime._goal_progress(state) == {"goal_updates": []}
    assert runtime.tools.call.call_count == 1
    runtime.tools.call.reset_mock()
    runtime.tools.call.side_effect = [
        {"autoSync": True, "goals": GOALS},
        {"applied": True, "checkIn": {"id": 1, "goalId": 7}},
    ]
    result = await runtime._goal_progress(state)
    assert result["goal_updates"] == [{"id": 1, "goalId": 7}]
    name, args = runtime.tools.call.call_args.args
    assert name == "sync_goal_progress"
    assert (
        args["request_id"] == "turn"
        and args["username"] == "alice"
        and args["quote"] == state["message"]
    )
    assert not eligible_report("你好，今天心情怎么样？")
