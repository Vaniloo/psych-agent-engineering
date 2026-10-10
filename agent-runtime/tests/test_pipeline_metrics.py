from unittest.mock import AsyncMock

import pytest

from psych_agent_runtime.config import Settings
from psych_agent_runtime.graph import AgentGraphRuntime
from psych_agent_runtime.mcp_client import McpToolClient
from psych_agent_runtime.telemetry import end_run, start_run


@pytest.mark.asyncio
async def test_local_calls_and_new_pipeline_are_recorded_without_private_arguments():
    settings = Settings(
        tool_transport="local",
        router_provider="rules",
        executor_provider="rules",
        memory_provider="rules",
    )
    telemetry, token = start_run(settings, "metric-test")
    try:
        client = McpToolClient(settings, None, None)
        client.executor = AsyncMock()
        client.executor.execute.return_value = {"created": True, "goal": {"id": 7}}
        await client.call(
            "create_goal", {"username": "private-user", "session_id": 1, "request_id": "private-id"}
        )
        assert telemetry.capability_calls[0]["name"] == "create_goal"
        assert telemetry.capability_calls[0]["status"] == "ok"
        assert "private-user" not in str(telemetry.capability_calls)
        client.executor.execute.side_effect = RuntimeError("unavailable")
        with pytest.raises(RuntimeError):
            await client.call(
                "create_goal",
                {"username": "private-user", "session_id": 1, "request_id": "private-id"},
            )
        assert telemetry.capability_calls[1]["status"] == "error"
        runtime = AgentGraphRuntime.__new__(AgentGraphRuntime)
        runtime.settings = settings
        runtime._emit = lambda *args: None
        state = {
            "message": "帮我创建一个休息目标",
            "persistence_status": "saved",
            "plan": {"intent": "strategy", "routing": {"conversationKind": "counseling"}},
            "support_level": "self_help",
            "created_goals": [{"id": 7}],
            "tool_trace": [{"step": 1, "action": "get_goal_context", "provider": "laya", "fallback": ""}],
        }
        result = await runtime._finalize_metrics(state)
        metrics = result["metrics"]
        assert metrics["pipeline"]["executor"]["contract"] == "psych-executor-v2"
        assert metrics["pipeline"]["executor"]["decisions"][0]["provider"] == "laya"
        assert metrics["pipeline"]["goals"]["created"] == 1
        assert metrics["pipeline"]["memory"]["delivery"] == "queued"
        assert metrics["capabilityCalls"] == telemetry.capability_calls
    finally:
        end_run(token)
