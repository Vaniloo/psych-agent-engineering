from benchmarks.loop_probe import run_controller

"""Verify real graph task boundaries, failure isolation and avoided model calls."""

from dataclasses import replace
from unittest.mock import AsyncMock

import httpx
import pytest

from psych_agent_runtime.config import Settings
from psych_agent_runtime.graph import AgentGraphRuntime
from psych_agent_runtime.route_policy import plan_for_route
from psych_agent_runtime.tool_controller import ToolController


def split_settings():
    return Settings(
        executor_provider="laya",
        executor_goal_provider="qwen",
        executor_goal_base_url="http://goal-worker",
    )


@pytest.mark.asyncio
async def test_goal_calls_go_to_separate_worker_and_failure_never_uses_tool_model():
    controller = ToolController(split_settings())
    controller.model.evaluate = AsyncMock(side_effect=AssertionError("goal reached tool worker"))
    controller.goal_model.evaluate = AsyncMock(
        return_value={"decisions": {"goal_creation": {"accepted": True, "choice": "create"}}}
    )
    runtime = AgentGraphRuntime.__new__(AgentGraphRuntime)
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
        assert controller.goal_model.evaluate.call_args.args[1] == ["goal_creation"]
        controller.goal_model.evaluate = AsyncMock(side_effect=httpx.ConnectError("offline"))
        runtime.tools.call.reset_mock()
        result = await runtime._goal_creation(state)
        assert result["created_goals"] == []
        assert result["goal_creation_result"]["reason"] == "decision_unavailable"
        runtime.tools.call.assert_not_called()
        controller.model.evaluate.assert_not_called()
        controller.goal_model.evaluate.reset_mock()
        state["message"] = "如何创建一个休息目标？"
        assert await runtime._goal_creation(state) == {"created_goals": []}
        controller.goal_model.evaluate.assert_not_called()
    finally:
        await controller.close()


@pytest.mark.asyncio
async def test_goal_progress_preserves_owned_ids_and_preference_under_split():
    controller = ToolController(split_settings())
    controller.model.evaluate = AsyncMock(side_effect=AssertionError("wrong worker"))
    evaluate_goals = controller.goal_model.evaluate
    controller.goal_model.evaluate = AsyncMock(
        return_value={
            "decisions": {
                "goal_action": {"accepted": True, "choice": "action_0"},
                "goal_outcome": {"accepted": True, "choice": "done"},
            }
        }
    )
    runtime = AgentGraphRuntime.__new__(AgentGraphRuntime)
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
        assert controller.goal_model.evaluate.call_args.args[1] == ["goal_action", "goal_outcome"]
        arguments = runtime.tools.call.call_args.args[1]
        assert (arguments["goal_id"], arguments["action_id"], arguments["version"]) == (
            7,
            "walk",
            3,
        )
        controller.model.evaluate.assert_not_called()
        controller.goal_model.evaluate.reset_mock()
        runtime.tools.call.side_effect = None
        runtime.tools.call.return_value = {"autoSync": False, "goals": goals}
        assert await runtime._goal_progress(state) == {"goal_updates": []}
        controller.goal_model.evaluate.assert_not_called()
        with pytest.raises(ValueError, match="unsupported"):
            await evaluate_goals({}, ["next_action"])
    finally:
        await controller.close()


@pytest.mark.asyncio
async def test_no_permissions_or_consumed_permissions_do_not_call_models():
    controller = ToolController(split_settings())
    controller.model.evaluate = AsyncMock(
        return_value={
            "decisions": {"next_action": {"choice": "search_knowledge", "accepted": True}}
        }
    )
    controller.goal_model.evaluate = AsyncMock(
        side_effect=AssertionError("tool reached goal worker")
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
        controller.goal_model.evaluate.assert_not_called()
    finally:
        await controller.close()


@pytest.mark.asyncio
async def test_inherited_worker_closes_once_and_explicit_rules_do_not_inherit():
    inherited = ToolController(Settings(executor_provider="laya", executor_goal_provider=""))
    inherited.model.close = AsyncMock()
    assert inherited.goal_model is inherited.model
    await inherited.close()
    inherited.model.close.assert_awaited_once()
    await inherited.model.client.aclose()
    rules = ToolController(Settings(executor_provider="laya", executor_goal_provider="rules"))
    try:
        assert rules.goal_model is None
    finally:
        await rules.close()


def test_split_configuration_must_be_explicit_and_valid():
    with pytest.raises(ValueError, match="BASE_URL"):
        ToolController(Settings(executor_goal_provider="qwen", executor_goal_base_url=""))
    with pytest.raises(ValueError, match="PROVIDER"):
        ToolController(Settings(executor_goal_provider="unknown"))


@pytest.mark.asyncio
async def test_runtime_manifest_reports_goal_worker_failure_separately(monkeypatch):
    from psych_agent_runtime import api

    settings = replace(
        split_settings(),
        executor_base_url="http://tool-worker",
        router_provider="rules",
        memory_provider="rules",
    )
    monkeypatch.setattr(api, "settings", settings)
    client = AsyncMock()
    client.__aenter__.return_value = client

    async def get(url):
        if url.startswith("http://goal-worker"):
            raise httpx.ConnectError("offline")
        return httpx.Response(
            200,
            request=httpx.Request("GET", url),
            json={
                "profile": "executor",
                "contractVersion": "psych-executor-v2",
                "ready": True,
                "model": "laya-test",
                "tasks": {"next_action": True, "goal_creation": True},
            },
        )

    client.get.side_effect = get
    monkeypatch.setattr(api.httpx, "AsyncClient", lambda **kwargs: client)
    result = await api.runtime_info()
    executor = result["profiles"][1]
    assert executor["status"] == "ready" and executor["provider"] == "laya"
    assert executor["tasks"] == executor["enabledTasks"] == ["next_action"]
    assert executor["mode"] == "task_split"
    assert executor["goalDecisions"]["provider"] == "qwen"
    assert executor["goalDecisions"]["status"] == "unavailable"
    assert set(executor["goalDecisions"]["tasks"]) == {
        "goal_action",
        "goal_outcome",
        "goal_creation",
    }
