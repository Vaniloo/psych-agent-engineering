from benchmarks.loop_probe import run_controller

"""Verify real graph task boundaries, failure isolation and avoided model calls."""

from unittest.mock import AsyncMock

import pytest

from benchmarks.runtime_fixture import make_runtime

from psych_agent_runtime.config import Settings
from psych_agent_runtime.route_policy import plan_for_route
from psych_agent_runtime.tool_controller import ToolController


@pytest.mark.asyncio
async def test_explicit_goal_creation_never_calls_read_executor():
    controller = ToolController(Settings(executor_provider="public_qwen"))
    controller.model.evaluate = AsyncMock(side_effect=AssertionError("goal reached tool worker"))
    runtime = make_runtime()
    runtime.tool_controller = controller
    runtime._emit = lambda *args: None
    runtime.tools = AsyncMock()
    runtime.tools.call.return_value = {"created": True, "goal": {"id": 7}}
    state = {
        "username": "alice",
        "session_id": 1,
        "request_id": "split",
        "message": "帮我创建一个休息目标",
        "persistence_status": "saved",
    }
    try:
        assert await runtime._goal_creation(state) == {"created_goals": [{"id": 7}]}
        runtime.tools.call.reset_mock()
        state["message"] = "如何创建一个休息目标？"
        assert await runtime._goal_creation(state) == {"created_goals": []}
        controller.model.evaluate.assert_not_called()
    finally:
        await controller.close()


@pytest.mark.asyncio
async def test_rule_progress_preserves_owned_ids_and_preference():
    controller = ToolController(Settings(executor_provider="public_qwen"))
    controller.model.evaluate = AsyncMock(side_effect=AssertionError("wrong worker"))
    runtime = make_runtime()
    runtime.tool_controller = controller
    runtime._emit = lambda *args: None
    runtime.tools = AsyncMock()
    goals = [
        {
            "id": 7,
            "version": 3,
            "title": "休息",
            "status": "active",
            "actions": [{"id": "walk", "title": "出去散步", "status": "planned"}],
        }
    ]
    runtime.tools.call.side_effect = [
        {"autoSync": True, "goals": goals},
        {"applied": True, "checkIn": {"id": 8}},
    ]
    state = {
        "username": "alice",
        "session_id": 1,
        "request_id": "split",
        "message": "我今天已经出去散步了。",
        "persistence_status": "saved",
    }
    try:
        result = await runtime._goal_progress(state)
        assert result["goal_updates"] == [{"id": 8}]
        assert result["goal_sync_result"]["applied"] is True
        arguments = runtime.tools.call.call_args.args[1]
        assert (arguments["goal_id"], arguments["action_id"], arguments["version"]) == (
            7,
            "walk",
            3,
        )
        controller.model.evaluate.assert_not_called()
        runtime.tools.call.side_effect = None
        runtime.tools.call.return_value = {"autoSync": False, "goals": goals}
        assert await runtime._goal_progress(state) == {"goal_updates": []}
    finally:
        await controller.close()


@pytest.mark.asyncio
async def test_no_permissions_or_consumed_permissions_do_not_call_models():
    controller = ToolController(Settings(executor_provider="public_qwen"))
    controller.model.evaluate = AsyncMock(
        return_value={
            "decisions": {"next_action": {"choice": "search_knowledge", "accepted": True}}
        }
    )
    invoke = AsyncMock(return_value={"hits": []})
    try:
        plan = plan_for_route("chat", "你好", "test")
        await run_controller(controller, plan, "你好", invoke, lambda *args: None)
        controller.model.evaluate.assert_not_called()
        invoke.assert_not_called()
        plan = plan_for_route("knowledge", "问题", "test")
        plan.allowed_tools = ["search_knowledge"]
        results, trace = await run_controller(controller, plan, "问题", invoke, lambda *args: None)
        assert list(results) == ["search_knowledge"]
        controller.model.evaluate.assert_awaited_once()
        assert trace[-1]["action"] == "finish" and trace[-1]["provider"] == "policy"
    finally:
        await controller.close()
