from unittest.mock import AsyncMock

import pytest

from benchmarks.runtime_fixture import make_runtime

from psych_agent_runtime.config import Settings
from psych_agent_runtime.mcp_client import McpToolClient
from psych_agent_runtime.telemetry import end_run, start_run


@pytest.mark.asyncio
async def test_local_calls_and_new_pipeline_are_recorded_without_private_arguments():
    settings = Settings(
        tool_transport="local",
        executor_provider="rules",
    )
    telemetry, token = start_run(settings, "metric-test")
    try:
        client = McpToolClient(settings, None, None)
        client.executor = AsyncMock()
        client.executor.execute.return_value = {"evidence": []}
        await client.call(
            "memory_search", {"username": "private-user", "query": "近况"}
        )
        assert telemetry.capability_calls[0]["name"] == "memory_search"
        assert telemetry.capability_calls[0]["status"] == "ok"
        assert "private-user" not in str(telemetry.capability_calls)
        client.executor.execute.side_effect = RuntimeError("unavailable")
        with pytest.raises(RuntimeError):
            await client.call(
                "memory_search",
                {"username": "private-user", "query": "近况"},
            )
        assert telemetry.capability_calls[1]["status"] == "error"
        runtime = make_runtime()
        runtime.settings = settings
        runtime._emit = lambda *args: None
        state = {
            "message": "请查看我的心理报告",
            "persistence_status": "saved",
            "plan": {"intent": "strategy", "routing": {"conversationKind": "counseling"}},
            "support_level": "self_help",
            "tool_trace": [{"step": 1, "action": "memory_search", "provider": "laya", "fallback": ""}],
        }
        result = await runtime._finalize_metrics(state)
        metrics = result["metrics"]
        assert metrics["pipeline"]["executor"]["contract"] == "rules"
        assert metrics["pipeline"]["executor"]["decisions"][0]["provider"] == "laya"
        assert metrics["pipeline"]["memory"]["delivery"] == "queued"
        assert metrics["pipeline"]["memory"]["contract"] == "bm25-owned-index"
        assert metrics["capabilityCalls"] == telemetry.capability_calls
    finally:
        end_run(token)
