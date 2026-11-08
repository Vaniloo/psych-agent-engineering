from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from benchmarks.runtime_fixture import make_runtime

from psych_agent_runtime.goal_operations import GoalOperations
from psych_agent_runtime.response_context import _project


def state():
    return {"username": "alice", "session_id": 2, "request_id": "saved-turn", "message": "第一步搞定了",
        "turn_acceptance": {"accepted": True}, "goal_gate_preview": {
            "mode": "model", "operation": "progress", "approval": "test-proof",
            "selection": {"goalId": 7, "actionId": "first", "version": 0, "outcome": "done"}},
        "tool_results": {"get_goal_context": {"autoSync": True, "goals": [{"id": 7, "version": 0}]}}}


@pytest.mark.asyncio
async def test_only_committed_done_advances_and_emits_pending_then_actual_next_step():
    entry = {"id": 9, "goalId": 7, "outcome": "done"}
    advancement = {"available": True, "goal": {"id": 7, "version": 2, "status": "active"},
                   "reflection": {"goalCompleted": False, "nextStep": "明天尝试两分钟"}}
    tools = SimpleNamespace(catalog=SimpleNamespace(refresh=AsyncMock()), call=AsyncMock(side_effect=[{"applied": True, "checkIn": entry}, advancement]))
    events = []
    result = await GoalOperations(tools, None, lambda e, d: events.append((e, d))).sync_progress(state())
    assert tools.call.call_args.args == ("advance_goal", {"username": "alice", "session_id": 2,
        "request_id": "saved-turn", "goal_id": 7, "check_in_id": 9})
    assert result["goal_updates"][0]["reflection"]["nextStep"] == "明天尝试两分钟"
    progress = [d["updates"][0] for e, d in events if e == "goal_progress"]
    assert [p["planningStatus"] for p in progress] == ["pending", "ready"]
    assert all(p["id"] == 9 for p in progress)


@pytest.mark.asyncio
async def test_planning_failure_preserves_saved_completion_and_partial_does_not_advance():
    tools = SimpleNamespace(catalog=SimpleNamespace(refresh=AsyncMock()), call=AsyncMock(side_effect=[
        {"applied": True, "checkIn": {"id": 9, "goalId": 7, "outcome": "done"}}, RuntimeError("offline")]))
    result = await GoalOperations(tools, None, lambda *_: None).sync_progress(state())
    assert result["goal_sync_result"]["applied"] is True
    assert result["goal_updates"][0]["outcome"] == "done"
    assert result["goal_updates"][0]["planningStatus"] == "unavailable"
    tools.call = AsyncMock(return_value={"applied": True, "checkIn": {"id": 10, "goalId": 7, "outcome": "partial"}})
    result = await GoalOperations(tools, None, lambda *_: None).sync_progress(state())
    assert result["goal_updates"][0]["outcome"] == "partial"
    assert tools.call.await_count == 1
    tools.call = AsyncMock(return_value={"applied": False, "reason": "disabled"})
    result = await GoalOperations(tools, None, lambda *_: None).sync_progress(state())
    assert not result["goal_updates"] and tools.call.await_count == 1


@pytest.mark.asyncio
async def test_loop_response_receives_persisted_advancement_instead_of_prewrite_goal_snapshot():
    runtime = make_runtime()
    runtime._emit = lambda *_: None
    runtime.tools = SimpleNamespace(catalog=SimpleNamespace(refresh=AsyncMock()), call=AsyncMock(return_value={"accepted": True, "completed": False}))
    runtime._goal_creation = AsyncMock(return_value={"created_goals": []})
    goal = {"id": 7, "version": 2, "status": "completed"}
    advance = {"available": True, "goal": goal, "reflection": {"goalCompleted": True}}
    runtime._goal_progress = AsyncMock(return_value={"goal_updates": [{"id": 9}],
        "goal_sync_result": {"applied": True, "advancement": advance}})
    result = await runtime._loop_goal_write({**state(), "loop": {}})
    assert result["tool_results"]["advance_goal"] == advance
    assert result["tool_results"]["get_goal_context"]["goals"] == [goal]


def test_response_projection_keeps_planning_receipt_without_repeating_large_goal_history():
    goal = {"id": 7, "title": "学习Python", "status": "active", "actions": ["HUGE_HISTORY" * 500]}
    reflection = {"nextStep": "明天再学一个新知识点", "goalCompleted": False}
    projected = _project("advance_goal", {"available": True, "goal": goal, "reflection": reflection})
    assert projected["reflection"] == reflection and "actions" not in projected["goal"]
    update = {"id": 9, "outcome": "done", "goal": goal, "reflection": reflection}
    assert "goal" not in _project("sync_goal_progress", {"applied": True, "updates": [update]})["updates"][0]
