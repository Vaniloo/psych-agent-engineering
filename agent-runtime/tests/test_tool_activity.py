import json
from unittest.mock import AsyncMock

import pytest
from langgraph.graph import END, START, StateGraph

from psych_agent_runtime.capabilities import normalize_result
from psych_agent_runtime.config import Settings
from psych_agent_runtime.graph import AgentGraphRuntime
from psych_agent_runtime.mcp_client import McpToolClient


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "name,result,status,summary",
    [
        ("search_knowledge", {"hits": [{"text": "private snippet"}]}, "ok", "1 条"),
        ("memory_search", {"evidence": []}, "ok", "没有找到"),
        ("create_goal", {"created": False, "reason": "private reason"}, "no_change", "未创建"),
        ("sync_goal_progress", {"applied": False}, "no_change", "未更新"),
        ("advance_goal", {"available": False}, "error", "无法"),
        ("advance_goal", {"available": True, "reflection": {"goalCompleted": True}}, "ok", "目标已达成"),
        ("get_dashboard", {"unavailable": True}, "error", "无法"),
    ],
)
async def test_events_pair_actual_calls_and_never_emit_private_payloads(
    name, result, status, summary
):
    events = []
    client = McpToolClient(
        Settings(), None, None, emit=lambda event, data: events.append((event, data))
    )

    async def execute(*args):
        assert events[0][1]["status"] == "running"
        return result

    client._call = execute
    assert await client.call(name, {"username": "private-user", "quote": "private quote"}) == result
    assert len(events) == 2
    started, finished = [data for event, data in events]
    assert all(event == "tool_activity" for event, _ in events)
    assert started["callId"] == finished["callId"]
    assert finished["status"] == status and summary in finished["summary"]
    assert finished["durationMs"] >= 0
    assert "private" not in json.dumps(events)


@pytest.mark.asyncio
async def test_failures_end_call_and_system_tools_are_not_public_activity():
    events = []
    client = McpToolClient(Settings(), None, None, emit=lambda event, data: events.append(data))
    client._call = AsyncMock(side_effect=RuntimeError("private URL and secret in exception"))
    with pytest.raises(RuntimeError):
        await client.call("get_goal_context", {})
    assert [e["status"] for e in events] == ["running", "error"]
    assert "private" not in str(events)
    events.clear()
    client._call = AsyncMock(return_value={"saved": True})
    await client.call("persist_turn", {})
    await client.call("risk_scan", {})
    assert events == []


@pytest.mark.asyncio
async def test_activity_is_delivered_over_langgraph_custom_stream_with_unique_call_ids():
    client = McpToolClient(Settings(), None, None, emit=AgentGraphRuntime._emit)
    client._call = AsyncMock(return_value={"goals": []})

    async def node(state):
        await client.call("get_goal_context", {})
        await client.call("get_goal_context", {})
        return state

    graph = StateGraph(dict)
    graph.add_node("tools", node)
    graph.add_edge(START, "tools")
    graph.add_edge("tools", END)
    events = [item async for item in graph.compile().astream({}, stream_mode="custom")]
    assert [e["data"]["status"] for e in events] == ["running", "ok", "running", "ok"]
    assert events[0]["data"]["callId"] == events[1]["data"]["callId"]
    assert events[0]["data"]["callId"] != events[2]["data"]["callId"]


@pytest.mark.asyncio
async def test_display_failure_does_not_fail_or_repeat_a_committed_write():
    def unavailable(event, data):
        raise RuntimeError("client gone")

    client = McpToolClient(Settings(), None, None, emit=unavailable)
    client._call = AsyncMock(return_value={"created": True, "goal": {"actions": None}})
    result = await client.call("create_goal", {})
    assert result["created"] is True
    client._call.assert_awaited_once()


def test_qwen_memory_crosses_typed_tool_boundary_and_retains_serving_metadata():
    result = normalize_result("memory_search", {
        "controller": "qwen", "evidence": [], "status": "controlled", "rounds": 1,
        "rerank": {"status": "completed", "poolCount": 32, "semanticAdded": 4},
        "sourceGuard": {"otherPersonBlocked": 2}, "decisionMode": "batch", "candidateCount": 12,
    })
    assert result["controller"] == "qwen"
    assert result["rerank"]["semanticAdded"] == 4
    assert result["sourceGuard"]["otherPersonBlocked"] == 2
    assert result["decisionMode"] == "batch" and result["candidateCount"] == 12
