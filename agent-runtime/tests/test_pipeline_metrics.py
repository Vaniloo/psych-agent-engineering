from unittest.mock import AsyncMock

import pytest

from benchmarks.runtime_fixture import make_runtime

from psych_agent_runtime.config import Settings
from psych_agent_runtime.mcp_client import McpToolClient
from psych_agent_runtime.telemetry import end_run, start_run


@pytest.mark.asyncio
@pytest.mark.parametrize("memory_contract", ["psych-memory-v2", "psych-memory-v4"])
async def test_local_calls_and_new_pipeline_are_recorded_without_private_arguments(memory_contract):
    settings = Settings(
        tool_transport="local",
        router_provider="rules",
        executor_provider="rules",
        memory_provider="rules",
        memory_contract_version=memory_contract,
    )
    telemetry, token = start_run(settings, "metric-test")
    try:
        client = McpToolClient(settings, None, None)
        client.executor = AsyncMock()
        client.executor.execute.return_value = {"reports": []}
        await client.call(
            "get_dashboard", {"username": "private-user"}
        )
        assert telemetry.capability_calls[0]["name"] == "get_dashboard"
        assert telemetry.capability_calls[0]["status"] == "ok"
        assert "private-user" not in str(telemetry.capability_calls)
        client.executor.execute.side_effect = RuntimeError("unavailable")
        with pytest.raises(RuntimeError):
            await client.call(
                "get_dashboard",
                {"username": "private-user"},
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
            "tool_trace": [{"step": 1, "action": "get_dashboard", "provider": "laya", "fallback": ""}],
        }
        result = await runtime._finalize_metrics(state)
        metrics = result["metrics"]
        assert metrics["pipeline"]["executor"]["contract"] == "psych-executor-v2"
        assert metrics["pipeline"]["executor"]["decisions"][0]["provider"] == "laya"
        assert metrics["pipeline"]["memory"]["delivery"] == "queued"
        assert metrics["pipeline"]["memory"]["contract"] == memory_contract
        assert metrics["capabilityCalls"] == telemetry.capability_calls
    finally:
        end_run(token)
