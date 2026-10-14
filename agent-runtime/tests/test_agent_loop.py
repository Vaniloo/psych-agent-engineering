"""Regressions for query recovery, permission boundaries and graph write ordering."""

import time
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from psych_agent_runtime.config import Settings
from psych_agent_runtime.graph import AgentGraphRuntime
from psych_agent_runtime.route_policy import plan_for_route
from psych_agent_runtime.schemas import AgentRequest, Plan
from psych_agent_runtime.tool_controller import ToolController


@pytest.mark.asyncio
async def test_empty_retrieval_can_change_query_once_and_keeps_owned_scope():
    c = ToolController(Settings())
    plan = Plan(
        intent="memory",
        allowed_tools=["memory_search"],
        required_tools=["memory_search"],
        max_tool_calls=1,
    )
    loop = c.start_loop(
        plan,
        "我之前说过喜欢什么？",
        {"memory_search": {"username": "alice", "query": "偏好", "limit": 3}},
    )
    invoke = AsyncMock(
        side_effect=[
            {"status": "empty", "evidence": []},
            {"status": "sufficient", "evidence": [{"role": "user", "content": "我喜欢散步"}]},
        ]
    )
    repair = AsyncMock(return_value={"tool": "memory_search", "arguments": {"query": "喜欢 爱好"}})
    for _ in range(3):
        loop = await c.decide(loop, lambda *_: None, repair)
        if loop["next"]["tool"] == "finish":
            break
        loop = c.observe(await c.execute(loop, invoke), lambda *_: None)
    assert loop["calls"] == 2 and len(loop["observations"]) == 2
    assert loop["results"]["memory_search"]["status"] == "sufficient"
    assert invoke.await_args_list[1].args[1]["username"] == "alice"
    assert invoke.await_args_list[1].args[1]["query"] == "喜欢 爱好"
    repair.assert_awaited_once()
    assert loop["next"]["tool"] == "finish"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "recovery",
    [
        {"tool": "create_goal", "arguments": {}},
        {"tool": "memory_search", "arguments": {"query": "更好查询", "username": "bob"}},
        {"tool": "memory_search", "arguments": {"query": "偏好"}},
    ],
)
async def test_recovery_cannot_write_change_identity_or_repeat_same_parameters(recovery):
    c = ToolController(Settings())
    loop = c.start_loop(
        Plan(allowed_tools=["memory_search"], required_tools=["memory_search"], max_tool_calls=1),
        "偏好",
        {"memory_search": {"username": "alice", "query": "偏好"}},
    )
    invoke = AsyncMock(return_value={"evidence": []})
    loop = await c.decide(loop, lambda *_: None)
    loop = c.observe(await c.execute(loop, invoke), lambda *_: None)
    repair = AsyncMock(return_value=recovery)
    loop = await c.decide(loop, lambda *_: None, repair)
    assert loop["next"]["tool"] == "finish" and loop["calls"] == 1
    loop = await c.decide(loop, lambda *_: None, repair)
    repair.assert_awaited_once()
    invoke.assert_awaited_once()


def test_recovery_preserves_explicit_completed_goal_filter():
    with pytest.raises(ValueError):
        ToolController.recovery_arguments(
            "get_goal_context", {"username": "alice", "status": "completed"}, {"status": None}
        )


@pytest.mark.asyncio
async def test_deadline_and_four_call_ceiling_prevent_extra_calls():
    c = ToolController(Settings())
    names = ["search_knowledge", "memory_search", "get_dashboard", "get_goal_context"]
    loop = c.start_loop(
        Plan(allowed_tools=names, required_tools=names, max_tool_calls=4), "综合查看"
    )
    invoke = AsyncMock(return_value={"hits": []})
    repair = AsyncMock(return_value={"tool": "search_knowledge", "arguments": {"query": "重试"}})
    while True:
        loop = await c.decide(loop, lambda *_: None, repair)
        if loop["next"]["tool"] == "finish":
            break
        loop = c.observe(await c.execute(loop, invoke), lambda *_: None)
    assert loop["calls"] == 4
    repair.assert_not_awaited()
    loop = c.start_loop(Plan(allowed_tools=["search_knowledge"]), "测试")
    loop["deadline"] = time.time() - 1
    loop = await c.decide(loop, lambda *_: None, repair)
    assert loop["next"]["tool"] == "finish"
    assert loop["trace"][-1]["fallback"] == "deadline_exhausted"


@pytest.mark.asyncio
async def test_graph_executes_and_observes_goal_save_before_reply_and_final_persistence():
    r = AgentGraphRuntime.__new__(AgentGraphRuntime)
    r.settings = Settings(memory_context_recall_enabled=False)
    r.tool_controller = ToolController(r.settings)
    r.ollama = SimpleNamespace(chat_json=AsyncMock(return_value={"tool": "finish"}))
    plan = plan_for_route(
        "chat",
        "",
        "test",
        {"conversationKind": "chat", "riskDecision": {"accepted": True, "riskBand": "low"}},
    )
    r.planner = SimpleNamespace(plan=AsyncMock(return_value=plan))
    events, calls = [], []
    r._emit = lambda name, data: events.append((name, data))
    goal = {
        "id": 7,
        "title": "写一句话",
        "actions": [{"title": "打开笔记写一个句子", "status": "planned"}],
        "completionCriteria": "保存一句话",
    }

    async def call(name, arguments):
        calls.append(name)
        if name == "persist_turn":
            assert "已为你创建" in arguments["assistant_reply"]
        return {
            "risk_scan": {"highRisk": False},
            "get_user_context": {"username": "alice", "sessionId": 1},
            "begin_turn": {"accepted": True, "completed": False, "assistantReply": ""},
            "create_goal": {"created": True, "goal": goal},
            "persist_turn": {"saved": True},
        }[name]

    r.tools = SimpleNamespace(call=call)
    r.graph = r._build_graph().compile()
    response = await r.invoke(
        AgentRequest(
            username="alice",
            sessionId=1,
            message="帮我创建一个目标，写一句话。",
            requestId="loop-test",
        )
    )
    assert calls == ["risk_scan", "get_user_context", "begin_turn", "create_goal", "persist_turn"]
    assert response.created_goals == [goal]
    assert "已为你创建" in response.reply
    assert next(i for i, (n, _) in enumerate(events) if n == "goal_created") < next(
        i for i, (n, _) in enumerate(events) if n == "token"
    )
    assert response.metrics.pipeline["loop"]["goalOperationAttempted"]
    nodes = r.graph.get_graph().nodes
    assert "agent_observe" in nodes and "knowledge_workflow" not in nodes


@pytest.mark.asyncio
async def test_failed_command_receipt_prevents_writes_and_success_claim():
    r = AgentGraphRuntime.__new__(AgentGraphRuntime)
    r.settings = Settings()
    r._emit = lambda *_: None
    r.ollama = None  # A failed write must return its actual result without generation.
    r.tools = SimpleNamespace(call=AsyncMock(side_effect=RuntimeError("database offline")))
    state = {
        "username": "alice",
        "session_id": 1,
        "request_id": "one",
        "message": "创建一个写一句话的目标",
        "loop": {"phase": "read"},
        "context": {"username": "alice", "sessionId": 1},
        "plan": Plan().model_dump(),
    }
    changes = await r._loop_goal_write(state)
    assert changes["created_goals"] == []
    r.tools.call.assert_awaited_once()
    reply = await r._respond({**state, **changes})
    assert "没有确认目标保存成功" in reply["reply"]
    assert "已为你创建" not in reply["reply"]


def test_compound_read_preserves_completed_filter_and_does_not_become_progress():
    from psych_agent_runtime.goal_progress import eligible_report
    from psych_agent_runtime.route_policy import guarantee_requested_reads

    message = "请查看我的心理报告、我完成的目标，并回顾我以前说过的偏好；再解释什么是焦虑。"
    plan = guarantee_requested_reads(plan_for_route("chat", message, "test"), message)
    assert plan.routing["goalStatusFilter"] == "completed"
    assert not eligible_report(message)
    assert not eligible_report("看一下我完成了什么目标")
    assert not eligible_report("总结已完成的目标「写一句话」")
    assert eligible_report("我今天已经写下一句话了。")


@pytest.mark.asyncio
async def test_invalid_structured_recovery_is_bounded_and_does_not_crash_loop():
    r = AgentGraphRuntime.__new__(AgentGraphRuntime)
    r.ollama = SimpleNamespace(chat_json=AsyncMock(return_value=[]))
    c = ToolController(Settings())
    loop = c.start_loop(
        Plan(allowed_tools=["search_knowledge"], required_tools=["search_knowledge"]),
        "解释焦虑",
        {"search_knowledge": {"query": "解释焦虑"}},
    )
    invoke = AsyncMock(return_value={"hits": []})
    loop = await c.decide(loop, lambda *_: None)
    loop = c.observe(await c.execute(loop, invoke), lambda *_: None)
    loop = await c.decide(loop, lambda *_: None, r._refine_loop)
    assert loop["next"]["tool"] == "finish"
    assert loop["trace"][-1]["fallback"] == "recovery_unavailable_or_invalid"
    invoke.assert_awaited_once()


@pytest.mark.asyncio
async def test_completed_request_replays_actual_reply_without_goal_write_or_memory_job():
    r = AgentGraphRuntime.__new__(AgentGraphRuntime)
    r.settings = Settings()
    r.ollama = None  # A completed receipt does not require the generation service.
    r._emit = lambda *_: None
    r.tools = SimpleNamespace(
        call=AsyncMock(
            return_value={"accepted": True, "completed": True, "assistantReply": "目标已保存。"}
        )
    )
    state = {
        "username": "alice",
        "session_id": 1,
        "request_id": "replay",
        "message": "帮我创建一个休息目标",
        "loop": {"phase": "read"},
        "context": {"username": "alice", "sessionId": 1},
        "plan": Plan().model_dump(),
    }
    changes = await r._loop_goal_write(state)
    r.tools.call.assert_awaited_once()
    state.update(changes)
    reply = await r._respond(state)
    assert reply["reply"] == "目标已保存。"
    assert await r._persist_turn(state) == {"persistence_status": "saved"}
    r.tools.call.assert_awaited_once()
