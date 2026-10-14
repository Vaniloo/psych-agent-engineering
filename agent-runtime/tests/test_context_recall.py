from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from benchmarks.loop_probe import run_read_loop
from psych_agent_runtime.capabilities import CapabilityExecutor, normalize_result
from psych_agent_runtime.config import Settings
from psych_agent_runtime.context_recall import background_query, with_context_recall
from psych_agent_runtime.graph import AgentGraphRuntime
from psych_agent_runtime.memory import MemoryService, MemoryStore
from psych_agent_runtime.route_policy import plan_for_route
from psych_agent_runtime.schemas import AgentContext
from psych_agent_runtime.token_budget import ContextBudgetManager
from psych_agent_runtime.tool_activity import result_summary


def context(count=0):
    return AgentContext(
        username="alice",
        sessionId=9,
        messages=[{"role": "user", "content": "正在讨论当前问题"}] * count,
    )


@pytest.mark.parametrize(
    "message", ["我最近有点累，想放松一下。", "上次那件事还是让我困扰。", "继续"]
)
def test_thin_conversation_opens_one_bounded_background_read(message):
    plan = with_context_recall(plan_for_route("chat", message, "test"), message, context())
    assert plan.required_tools == ["memory_search"] and plan.max_tool_calls == 1
    assert plan.routing["contextRecall"]["mode"] == "cross_session"


@pytest.mark.parametrize(
    "message",
    [
        "你好",
        "谢谢你",
        "什么是CBT？",
        "解释一下JWT是什么。",
        "不要查我以前的记录，我最近有点累。",
        "只聊这次的感受，我最近有点累。",
        "帮我创建一个休息目标",
        "我今天出去散步五分钟了。",
        "例如他说最近压力大，怎么回答？",
    ],
)
def test_optout_public_example_and_write_do_not_open_background(message):
    plan = with_context_recall(plan_for_route("chat", message, "test"), message, context())
    assert "contextRecall" not in plan.routing and plan.required_tools == []


def test_existing_local_context_and_full_tool_budget_are_kept():
    message = "我最近有点累，想放松一下。"
    plan = with_context_recall(plan_for_route("chat", message, "test"), message, context(3))
    assert "contextRecall" not in plan.routing
    plan = with_context_recall(plan_for_route("strategy", message, "test"), message, context())
    assert "contextRecall" not in plan.routing
    plan = with_context_recall(plan_for_route("knowledge", message, "test"), message, context())
    assert plan.required_tools == ["search_knowledge", "memory_search"] and plan.max_tool_calls == 2
    plan = with_context_recall(
        plan_for_route("chat", "", "test"), "上次面试那件事还是让我紧张。", context(3)
    )
    assert plan.routing["contextRecall"]["reason"] == "unresolved_continuation"


def test_relaxation_query_searches_missing_preferences_not_repeated_checkins(tmp_path):
    store = MemoryStore(str(tmp_path / "crowded.db"))
    seed(store, "alice", 1, "我喜欢听轻音乐放松。", 0)
    seed(store, "alice", 1, "我喜欢在安静的地方休息。", 1)
    for i in range(2, 32):
        seed(store, "alice", 2, "我今天出去散步了一点，还没完成五分钟。请简短回应。", i)
    query = background_query("我今天有点累，想找一种适合我的放松方式。请简短回应。")
    rows = store.candidates("alice", query, exclude_session_id=9)
    assert {"我喜欢听轻音乐放松。", "我喜欢在安静的地方休息。"} <= {r["content"] for r in rows}
    assert "面试" in background_query("上次面试有点紧张，请简短回应。")


def seed(store, owner, session, text, i):
    store.ingest(
        {
            "username": owner,
            "sessionId": session,
            "requestId": str(i),
            "userMessage": text,
            "assistantReply": "建议不是用户事实",
            "createdAt": f"2026-10-13T10:{i:02d}:00",
        }
    )


def test_exclusion_happens_before_topk_and_stays_within_owner(tmp_path):
    store = MemoryStore(str(tmp_path / "memory.db"))
    seed(store, "alice", 1, "我喜欢在河边慢慢散步。", 0)
    seed(store, "bob", 2, "我喜欢在海边休息。", 1)
    for i in range(2, 32):
        seed(store, "alice", 9, "我现在问如何休息。", i)
    assert store.has_other_session("alice", 9)
    assert not store.has_other_session("bob", 2)
    assert not store.has_other_session("charlie", 9)
    for rows in [
        store.candidates("alice", "休息", exclude_session_id=9),
        store.recent("alice", exclude_session_id=9),
    ]:
        assert rows and all(r["sessionId"] == 1 and r["role"] == "user" for r in rows)
        assert all("海边" not in r["content"] and "建议" not in r["content"] for r in rows)


@pytest.mark.asyncio
async def test_automatic_context_never_injects_unchecked_fallback(tmp_path):
    service = MemoryService(Settings(memory_store_path=str(tmp_path / "memory.db")))
    service.retrieve = AsyncMock(
        return_value={
            "evidence": [{"content": "未核实原话"}],
            "status": "lexical_fallback",
            "controller": "rules",
        }
    )
    try:
        result = await service.retrieve_context("alice", "我最近有点累", 9)
        assert result["evidence"] == [] and result["status"] == "unavailable"
        assert normalize_result("memory_search", result)["purpose"] == "context"
        assert "继续当前对话" in result_summary("memory_search", result)
        assert service.retrieve.call_args.kwargs["exclude_session_id"] == 9
    finally:
        await service.close()


@pytest.mark.asyncio
async def test_context_capability_requires_current_session_and_caps_delivery():
    memory = SimpleNamespace(
        retrieve_context=AsyncMock(return_value={"evidence": [], "purpose": "context"})
    )
    executor = CapabilityExecutor(None, None, memory)
    with pytest.raises(ValueError, match="current owned session"):
        await executor.execute(
            "memory_search", {"username": "alice", "query": "累", "purpose": "context"}
        )
    await executor.execute(
        "memory_search",
        {"username": "alice", "query": "累", "purpose": "context", "session_id": 9, "limit": 10},
    )
    memory.retrieve_context.assert_awaited_once_with("alice", "累", 9, 3)


@pytest.mark.asyncio
async def test_real_tool_loop_injects_owned_scope_and_skips_empty_other_sessions(tmp_path):
    store = MemoryStore(str(tmp_path / "memory.db"))
    runtime = AgentGraphRuntime.__new__(AgentGraphRuntime)
    runtime.settings = Settings()
    runtime.memory = SimpleNamespace(store=store)
    runtime._emit = lambda *_: None
    runtime.tools = SimpleNamespace(
        call=AsyncMock(return_value={"evidence": [], "purpose": "context"})
    )
    state = {
        "username": "alice",
        "session_id": 9,
        "context": context().model_dump(by_alias=True),
        "message": "我最近有点累，想放松一下。",
        "plan": plan_for_route("chat", "", "test").model_dump(),
    }
    result = await run_read_loop(runtime, state)
    runtime.tools.call.assert_not_awaited()
    seed(store, "alice", 1, "我喜欢安静散步", 0)
    result = await run_read_loop(runtime, state)
    runtime.tools.call.assert_awaited_once_with(
        "memory_search",
        {
            "username": "alice",
            "query": state["message"],
            "limit": 3,
            "purpose": "context",
            "session_id": 9,
        },
    )
    assert result["plan"]["intent"] == "chat"


@pytest.mark.asyncio
async def test_background_keeps_current_history_and_does_not_demand_absence_reply():
    seen = []

    async def stream(messages, **kwargs):
        seen.extend(messages)
        yield "回应当前感受"

    runtime = AgentGraphRuntime.__new__(AgentGraphRuntime)
    runtime.settings = Settings()
    runtime.context_budget = ContextBudgetManager(8192, 1200)
    runtime.ollama = SimpleNamespace(stream_chat=stream)
    runtime._emit = lambda *_: None
    state = {
        "context": {
            "username": "alice",
            "sessionId": 9,
            "messages": [{"role": "user", "content": "本轮我想聊工作。"}],
        },
        "message": "我最近有点累。",
        "request_id": "test",
        "plan": plan_for_route("chat", "", "test").model_dump(),
        "tool_results": {
            "memory_search": {
                "purpose": "context",
                "scope": "other_sessions_same_owner",
                "evidence": [],
                "status": "empty",
            }
        },
    }
    await runtime._respond(state)
    prompt = "\n".join(m["content"] for m in seen)
    assert "本轮我想聊工作" in prompt and "只作可选背景" in prompt
    assert "空结果" in prompt and "逐项检查问题中的每个事实" not in prompt
