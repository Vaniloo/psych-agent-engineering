from unittest.mock import AsyncMock

import pytest

from psych_agent_runtime.capabilities import CAPABILITIES, validate_arguments
from psych_agent_runtime.goal_creation import allow_creation, explicit_creation
from psych_agent_runtime.graph import AgentGraphRuntime


@pytest.mark.asyncio
async def test_explicit_permission_and_executor_abstention():
    assert await allow_creation("帮我创建一个目标，让拖延的工作开始。")
    for text in [
        "我想早点休息",
        "不要帮我创建目标",
        "如何创建目标？",
        "他说帮我创建目标",
        "agent有创建目标的工具吗",
    ]:
        assert not explicit_creation(text)
    model = AsyncMock()
    model.evaluate.return_value = {
        "decisions": {"goal_creation": {"accepted": False, "choice": "create"}}
    }
    assert not await allow_creation("帮我创建一个休息目标", model)
    model.evaluate.return_value["decisions"]["goal_creation"]["accepted"] = True
    assert await allow_creation("帮我创建一个休息目标", model)
    assert CAPABILITIES["create_goal"].scope == "goals:write:create"
    with pytest.raises(ValueError):
        validate_arguments(
            "create_goal",
            {"username": "alice", "session_id": 1, "request_id": "one", "title": "model supplied"},
        )


@pytest.mark.asyncio
async def test_creation_requires_saved_non_crisis_turn_and_emits_only_persisted_goal():
    runtime = AgentGraphRuntime.__new__(AgentGraphRuntime)
    runtime.tools = AsyncMock()
    events = []
    runtime._emit = lambda name, data: events.append((name, data))
    state = {
        "username": "alice",
        "session_id": 1,
        "request_id": "one",
        "message": "帮我创建一个休息目标",
        "persistence_status": "failed",
    }
    assert await runtime._goal_creation(state) == {"created_goals": []}
    runtime.tools.call.assert_not_called()
    state.update(persistence_status="saved", crisis=True)
    assert await runtime._goal_creation(state) == {"created_goals": []}
    runtime.tools.call.assert_not_called()
    state["crisis"] = False
    goal = {
        "id": 7,
        "title": "休息",
        "actions": [{"id": "one", "title": "走一走", "status": "planned"}],
    }
    runtime.tools.call.return_value = {"created": True, "goal": goal}
    assert await runtime._goal_creation(state) == {"created_goals": [goal]}
    runtime.tools.call.assert_awaited_once_with(
        "create_goal", {"username": "alice", "session_id": 1, "request_id": "one"}
    )
    assert ("goal_created", {"goals": [goal]}) in events
    runtime.tools.call.reset_mock()
    assert await runtime._goal_progress(state) == {"goal_updates": []}
    runtime.tools.call.assert_not_called()
    events.clear()
    runtime.tools.call.return_value = {"created": False, "reason": "plan_unavailable"}
    assert await runtime._goal_creation(state) == {"created_goals": []}
    assert not any(name == "goal_created" for name, data in events)
