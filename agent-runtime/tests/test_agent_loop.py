"""Regressions for query recovery, permission boundaries and graph write ordering."""

import time
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from benchmarks.runtime_fixture import make_runtime

from psych_agent_runtime.config import Settings
from psych_agent_runtime.schemas import Plan
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
        {"tool": "persist_turn", "arguments": {}},
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


@pytest.mark.asyncio
async def test_deadline_and_four_call_ceiling_prevent_extra_calls():
    c = ToolController(Settings(agent_loop_max_tool_calls=4))
    names = ["search_knowledge", "memory_search", "get_dashboard", "get_support_resources"]
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
async def test_invalid_structured_recovery_is_bounded_and_does_not_crash_loop():
    r = make_runtime()
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
