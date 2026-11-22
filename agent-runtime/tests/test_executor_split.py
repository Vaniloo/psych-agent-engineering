from benchmarks.loop_probe import run_controller

"""Verify real graph task boundaries, failure isolation and avoided model calls."""

from unittest.mock import AsyncMock

import pytest


from psych_agent_runtime.config import Settings
from psych_agent_runtime.route_policy import plan_for_route
from psych_agent_runtime.tool_controller import ToolController


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
