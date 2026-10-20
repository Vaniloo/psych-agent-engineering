import hashlib
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from psych_agent_runtime.goal_management import inspect_management
from psych_agent_runtime.goal_operations import GoalOperations
from psych_agent_runtime.graph import AgentGraphRuntime
from psych_agent_runtime.response_context import _project
from psych_agent_runtime.response_prompt import build_response_prompt
from psych_agent_runtime.schemas import Plan

GOAL = {"id": 7, "version": 2, "title": "每天学Java", "status": "active", "actions": [
    {"id": "first", "title": "选一个时间", "status": "done", "feedback": ["下午两小时"]},
    {"id": "second", "title": "准备学习资源", "status": "planned", "feedback": []}]}
MESSAGE = "第二步你优化一下，来点详细的"


@pytest.mark.asyncio
async def test_model_edit_resolves_original_position_preserves_ids_and_binds_exact_patch():
    model = SimpleNamespace(chat_json=AsyncMock(return_value={"operation": "revise", "goalIndex": 0,
        "evidence": MESSAGE, "edits": [{"position": 2, "title": "从手边教程选一节，在下午的时段完成一个能运行的小例子"}]}))
    result = await inspect_management(model, MESSAGE, [], [GOAL], 7)
    assert result["operation"] == "revise"
    patch = json.loads(result["editsJson"])
    assert patch[0]["actionId"] == "second"
    assert result["selection"]["outcome"] == hashlib.sha256(result["editsJson"].encode()).hexdigest()
    model_input = model.chat_json.call_args.args[0][-1]["content"]
    assert '"goalIndex": 0' in model_input and '"position": 2' in model_input
    assert '"actionId"' not in model_input


@pytest.mark.asyncio
@pytest.mark.parametrize("answer", [
    {"operation": "revise", "goalIndex": 0, "evidence": MESSAGE, "edits": [{"position": 1, "title": "篡改已完成"}]},
    {"operation": "revise", "goalIndex": 0, "evidence": MESSAGE, "edits": [{"position": 3, "title": "虚构步骤"}]},
    {"operation": "complete", "goalIndex": 0, "evidence": "助手声称完成", "edits": []},
    {"operation": "complete", "goalIndex": True, "evidence": MESSAGE, "edits": []},
    {"operation": "complete", "goalIndex": 0, "evidence": MESSAGE, "edits": [{"position": 2, "title": "不能改步骤"}]},
])
async def test_management_rejects_invalid_source_position_or_history_changes(answer):
    model = SimpleNamespace(chat_json=AsyncMock(return_value=answer))
    assert (await inspect_management(model, MESSAGE, [], [GOAL], 7))["operation"] == "unavailable"


@pytest.mark.asyncio
async def test_closure_uses_goal_scope_without_fabricating_step_completion():
    for operation in ("complete", "pause"):
        model = SimpleNamespace(chat_json=AsyncMock(return_value={"operation": operation,
            "goalIndex": 0, "evidence": MESSAGE, "edits": []}))
        result = await inspect_management(model, MESSAGE, [], [GOAL], 7)
        assert result["operation"] == operation and result["editsJson"] == "[]"
    model.chat_json.side_effect = RuntimeError("offline")
    assert (await inspect_management(model, MESSAGE, [], [GOAL], 7))["operation"] == "unavailable"


@pytest.mark.asyncio
async def test_loop_applies_management_once_refreshes_read_and_never_advances_after_close():
    final_goal = {**GOAL, "status": "completed", "version": 3}
    entry = {"id": 11, "goalId": 7, "outcome": "goal_completed"}
    management = {"applied": True, "goal": final_goal, "checkIn": entry, "operation": "complete"}
    runtime = AgentGraphRuntime.__new__(AgentGraphRuntime)
    events = []; runtime._emit = lambda e, d: events.append((e, d))
    runtime.tools = SimpleNamespace(call=AsyncMock(side_effect=[{"accepted": True, "completed": False}, management]))
    runtime._goal_creation = AsyncMock(return_value={"created_goals": []})
    runtime._goal_progress = AsyncMock(return_value={"goal_updates": []})
    state = {"username": "alice", "session_id": 2, "request_id": "r", "message": "整个目标完成了",
        "loop": {}, "goal_gate_preview": {"mode": "model", "operation": "complete", "approval": "proof",
            "selection": {"goalId": 7, "version": 2}, "editsJson": "[]"},
        "tool_results": {"get_goal_context": {"goals": [GOAL]}}}
    result = await runtime._loop_goal_write(state)
    assert [c.args[0] for c in runtime.tools.call.call_args_list] == ["begin_turn", "manage_goal"]
    assert result["tool_results"]["get_goal_context"]["goals"] == [final_goal]
    assert result["goal_updates"][0]["outcome"] == "goal_completed"
    assert _project("manage_goal", management)["goal"]["status"] == "completed"
    prompt = build_response_prompt({**state, **result}, Plan(), background_recall=False, goal_read=True)
    assert "停止追加步骤" in prompt
    runtime.tools.call = AsyncMock(side_effect=RuntimeError("unavailable"))
    failed = await GoalOperations(runtime.tools, None, lambda *_: None).manage({**state, "turn_acceptance": {"accepted": True}})
    assert failed["goal_management_result"]["applied"] is False and not failed["goal_updates"]


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["revise", "complete", "pause"])
async def test_management_reply_confirms_saved_result_instead_of_proposing_another_unsaved_edit(operation):
    from psych_agent_runtime.response import ResponseComposer
    composer = ResponseComposer.__new__(ResponseComposer)
    events = []; composer._emit = lambda e, d: events.append((e, d))
    result = await composer.generate({"message": MESSAGE, "context": {"username": "alice", "sessionId": 2},
        "tool_results": {"manage_goal": {"applied": True, "operation": operation, "goal": GOAL,
            "changedSteps": [{"position": 2, "title": "运行一个最小示例"}]}}})
    assert result["context_stats"]["mode"] == "goal_management_result"
    assert "如果你愿意" not in result["reply"] and "帮你保存" not in result["reply"]
    assert "运行一个最小示例" in result["reply"] if operation == "revise" else "不再自动追加" in result["reply"]


def test_closed_conversation_goal_is_preserved_apart_from_same_name_old_goals():
    projected = _project("get_goal_context", {"conversationGoalId": 7, "goals": [
        {**GOAL, "status": "completed"}, {**GOAL, "id": 8, "status": "paused"}]})
    assert projected["conversationGoal"] == {"title": GOAL["title"], "status": "completed"}


def test_partial_progress_read_is_explicit_saved_evidence_and_undone_records_are_excluded():
    projected = _project("get_goal_context", {"goals": [GOAL], "recentProgress": [
        {"id": 1, "goalId": 7, "goalTitle": GOAL["title"], "actionTitle": "准备学习资源",
         "outcome": "partial", "note": "选了一本教程，还没做完", "createdAt": "2026-10-20T08:00:00", "undone": False},
        {"id": 2, "goalTitle": GOAL["title"], "outcome": "done", "note": "撤销了", "undone": True}]})
    assert projected["recentProgress"] == [{"goalTitle": GOAL["title"], "actionTitle": "准备学习资源",
        "outcome": "partial", "note": "选了一本教程，还没做完", "createdAt": "2026-10-20T08:00:00", "saved": True}]
    assert projected["goals"][0]["actions"][1]["status"] == "planned"
