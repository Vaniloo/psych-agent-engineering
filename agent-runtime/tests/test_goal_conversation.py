from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from benchmarks.runtime_fixture import make_runtime

from psych_agent_runtime.capabilities import CapabilityExecutor
from psych_agent_runtime.config import Settings
from psych_agent_runtime.goal_context import goal_focus
from psych_agent_runtime.goal_creation import allow_creation
from psych_agent_runtime.planner import AgentPlanner
from psych_agent_runtime.response import ResponseComposer
from psych_agent_runtime.route_policy import plan_for_route
from psych_agent_runtime.schemas import AgentContext
from psych_agent_runtime.token_budget import ContextBudgetManager


@pytest.mark.asyncio
async def test_confirmed_progress_reply_uses_current_receipt_without_replaying_old_optout():
    captured = []

    async def stream(messages, **kwargs):
        captured.extend(messages)
        yield "这次已记下部分进展。"

    composer = ResponseComposer(Settings(), SimpleNamespace(stream_chat=stream),
                                ContextBudgetManager(8192, 1200), lambda *_: None)
    result = await composer.generate({"context": {"username": "alice", "sessionId": 1,
        "messages": [{"role": "user", "content": "那一次别给我同步旧记录"}]},
        "message": "第二步我做了一点", "plan": plan_for_route("chat", "", "test").model_dump(),
        "goal_operations_done": True, "tool_results": {"sync_goal_progress": {
            "applied": True, "updates": [{"actionTitle": "学一个小知识点", "outcome": "partial", "note": "第二步我做了一点"}]}}})
    assert "已记下" in result["reply"]
    assert not any("那一次别给我同步旧记录" in m["content"] for m in captured)
    assert captured[-1]["content"] == "第二步我做了一点"
    assert any('"outcome": "partial"' in m["content"] for m in captured)


def context():
    return AgentContext(
        username="alice",
        sessionId=1,
        messages=[
            {"role": "user", "content": "我想聊聊目标「准备Agent面试」的进展："},
            {
                "role": "assistant",
                "content": "旧的错误回复：目标已经结束，无法读取；不能创建目标。",
            },
        ],
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "message",
    [
        "我想聊聊目标「准备Agent面试」的进展：",
        "你总结一下",
        "你不是可以调用工具吗",
        "结束的这个目标还有哪些记录？",
        "你看看能不能设置一个新的下一步目标",
    ],
)
@pytest.mark.parametrize("route", ["chat", "memory", "knowledge", "clarify"])
async def test_named_goal_and_short_followups_force_owned_read_even_with_bad_route(message, route):
    planner = AgentPlanner(settings=Settings(router_provider="qwen"))
    planner.router.decide = AsyncMock(return_value={"accepted": True, "route": route})
    try:
        plan = await planner.plan(message, context())
        assert "get_goal_context" in plan.required_tools
        assert plan.routing["goalFocus"] == "准备Agent面试"
        assert plan.max_tool_calls <= 2
    finally:
        await planner.close()


@pytest.mark.parametrize(
    "message",
    [
        "今天天气不错",
        "不要查我的目标，随便聊聊",
        "例如他说要聊目标「准备Agent面试」",
        "换个话题，聊聊目标「别人的目标」",
        "帮我创建一个新的睡眠目标",
        "帮我创建目标「新的下一步目标」",
    ],
)
def test_unrelated_quotes_optout_and_new_topic_do_not_reuse_goal(message):
    assert goal_focus(message, context()) is None


@pytest.mark.asyncio
async def test_focused_goal_read_passes_only_owned_identity_and_title():
    backend = SimpleNamespace(goals=AsyncMock(return_value={"goals": []}))
    executor = CapabilityExecutor(backend, None)
    result = await executor.execute(
        "get_goal_context", {"username": "alice", "title": "准备Agent面试"}
    )
    backend.goals.assert_awaited_once_with("alice", title="准备Agent面试")
    assert result["actionLibrary"] == []


@pytest.mark.asyncio
async def test_completed_goal_evidence_and_feedback_replace_old_denial():
    captured = []

    async def stream(messages, **kwargs):
        captured.extend(messages)
        yield "依据实际步骤总结"

    runtime = make_runtime()
    runtime.settings = Settings()
    runtime.context_budget = ContextBudgetManager(8192, 1200)
    runtime.ollama = SimpleNamespace(stream_chat=stream)
    runtime._emit = lambda *_: None
    state = {
        "context": context().model_dump(by_alias=True),
        "message": "你总结一下",
        "request_id": "summary",
        "plan": plan_for_route("strategy", "", "test").model_dump(),
        "tool_results": {
            "get_goal_context": {
                "goals": [
                    {
                        "title": "准备Agent面试",
                        "status": "completed",
                        "completionCriteria": "完成一题完整回答",
                        "actions": [
                            {
                                "title": "说出工具权限控制",
                                "status": "done",
                                "feedback": ["第一遍紧张，第二遍更顺了"],
                            }
                        ],
                    }
                ]
            }
        },
    }
    await runtime._respond(state)
    prompt = "\n".join(m["content"] for m in captured)
    assert "第一遍紧张，第二遍更顺了" in prompt and "completed" in prompt
    assert "旧的错误回复" not in prompt


@pytest.mark.asyncio
async def test_next_goal_uses_verified_owned_source_but_unfound_source_is_not_passed():
    runtime = make_runtime()
    runtime._emit = lambda *_: None
    runtime.tools = SimpleNamespace(catalog=SimpleNamespace(refresh=AsyncMock()),
        call=AsyncMock(return_value={"created": True, "goal": {"id": 99}})
    )
    state = {
        "username": "alice",
        "session_id": 1,
        "request_id": "next",
        "message": "你看看能不能设置一个新的下一步目标",
        "goal_operations_done": True,
        "goal_intent": {"intent": "create", "goalText": "下一步目标"},
        "created_goals": [],
        "persistence_status": "saved",
        "plan": {"routing": {"goalFocus": "准备Agent面试"}},
        "tool_results": {
            "get_goal_context": {"goals": [{"title": "准备Agent面试", "status": "completed"}]}
        },
    }
    result = await runtime._goal_creation(state)
    assert result["created_goals"] == [{"id": 99}]
    runtime.tools.call.assert_awaited_once_with(
        "create_goal",
        {
            "username": "alice",
            "session_id": 1,
            "request_id": "next",
            "source_goal_title": "准备Agent面试",
        },
    )
    state["tool_results"] = {"get_goal_context": {"goals": []}}
    runtime.tools.call.reset_mock()
    result = await runtime._goal_creation(state)
    assert result["created_goals"] == []
    assert result["goal_creation_result"]["reason"] == "source_goal_not_found"
    runtime.tools.call.assert_not_awaited()


@pytest.mark.asyncio
async def test_polite_followup_is_canonicalized_only_after_reading_owned_source():
    model = SimpleNamespace(
        evaluate=AsyncMock(
            return_value={"decisions": {"goal_creation": {"accepted": True, "choice": "create"}}}
        )
    )
    message = "你看看能不能设置一个新的下一步目标"
    assert await allow_creation(message, model, has_owned_source=True)
    assert model.evaluate.call_args.args[0]["userExpression"] == "请设置一个新的下一步目标"
    await allow_creation(message, model, has_owned_source=False)
    assert model.evaluate.call_args.args[0]["userExpression"] == message
    model.evaluate.reset_mock()
    assert not await allow_creation(
        "他说你看看能不能设置一个新的下一步目标", model, has_owned_source=True
    )
    model.evaluate.assert_not_awaited()


@pytest.mark.asyncio
async def test_creation_failure_uses_actual_result_without_second_confirmation():
    runtime = make_runtime()
    runtime.settings = Settings()
    runtime.ollama = SimpleNamespace(
        stream_chat=AsyncMock(side_effect=AssertionError("Planning belongs to create_goal"))
    )
    emitted = []
    runtime._emit = lambda name, data: emitted.append((name, data))
    state = {
        "context": context().model_dump(by_alias=True),
        "message": "你看看能不能设置一个新的下一步目标",
        "goal_operations_done": True,
        "goal_intent": {"intent": "create", "goalText": "下一步目标"},
        "created_goals": [],
        "plan": plan_for_route("strategy", "", "test", {"goalFocus": "准备Agent面试"}).model_dump(),
        "tool_results": {
            "get_goal_context": {"goals": [{"title": "准备Agent面试", "status": "completed"}]}
        },
    }
    result = await runtime._respond(state)
    runtime.ollama.stream_chat.assert_not_called()
    assert not any(
        x in result["reply"] for x in ["已创建", "已保存", "要不要", "请确认", "无法创建"]
    )
    assert emitted[-1] == ("token", {"text": result["reply"]})
    assert result["context_stats"]["mode"] == "goal_operation_result"
    assert "没有确认目标保存成功" in result["reply"]
