from unittest.mock import AsyncMock

import pytest

from benchmarks.runtime_fixture import make_runtime

from psych_agent_runtime.capabilities import CAPABILITIES, validate_arguments
from psych_agent_runtime.goal_creation import allow_creation, explicit_creation


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
    runtime = make_runtime()
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
    result = await runtime._goal_creation(state)
    assert result["created_goals"] == []
    assert result["goal_creation_result"]["reason"] == "plan_unavailable"
    assert not any(name == "goal_created" for name, data in events)


@pytest.mark.asyncio
async def test_unavailable_goal_write_survives_loop_and_has_truthful_failure_reply():
    from types import SimpleNamespace

    import httpx

    from psych_agent_runtime.config import Settings
    from psych_agent_runtime.route_policy import plan_for_route

    runtime = make_runtime()
    runtime.settings = Settings()
    runtime.ollama = None  # Failure reply must not need a second model call.
    runtime._emit = lambda *_: None
    runtime.tools = SimpleNamespace(catalog=SimpleNamespace(refresh=AsyncMock()), call=AsyncMock(
        side_effect=[{"accepted": True, "completed": False}, httpx.ReadError("closed")]))
    state = {"username": "alice", "session_id": 1, "request_id": "creation-outage",
             "message": "帮我新建一个每天睡够八小时的目标", "loop": {"phase": "read"},
             "context": {"username": "alice", "sessionId": 1},
             "plan": plan_for_route("strategy", "", "test").model_dump()}
    changes = await runtime._loop_goal_write(state)
    assert changes["tool_results"]["create_goal"]["reason"] == "create_unavailable"
    assert runtime.tools.call.call_count == 2  # Turn accepted, then the write failed.
    result = await runtime._respond({**state, **changes})
    assert "创建服务暂时不可用" in result["reply"]
    assert "没有确认目标保存成功" in result["reply"] and "已为你创建" not in result["reply"]
