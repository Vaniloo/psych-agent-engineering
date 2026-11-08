import asyncio
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock

import pytest
from pydantic import ValidationError

from psych_agent_runtime.capabilities import normalize_result, validate_arguments
from psych_agent_runtime.config import Settings
from psych_agent_runtime.mcp_client import McpToolClient


def test_capability_schema_rejects_unknown_fields_and_out_of_range_limits():
    with pytest.raises(ValidationError):
        validate_arguments("search_knowledge", {"query": "睡眠", "username": "forged"})
    with pytest.raises(ValidationError):
        validate_arguments("search_knowledge", {"query": "睡眠", "limit": 1000})
    with pytest.raises(ValueError):
        validate_arguments("execute_shell", {})
    with pytest.raises(ValueError):
        normalize_result("persist_turn", {"saved": False})
    with pytest.raises(ValueError):
        normalize_result("risk_scan", {"highRisk": "false"})


@pytest.mark.parametrize(
    "name,args,replayed",
    [
        ("get_dashboard", {"username": "alice"}, True),
        (
            "persist_turn",
            {
                "username": "alice",
                "session_id": 1,
                "request_id": "request-1",
                "user_message": "你好",
                "assistant_reply": "你好",
            },
            False,
        ),
        ("crisis_support", {"username": "alice", "message": "测试"}, False),
    ],
)
def test_ambiguous_transport_failure_does_not_replay_writes(monkeypatch, name, args, replayed):
    import mcp
    import mcp.client.streamable_http

    @asynccontextmanager
    async def transport(*_a, **_k):
        yield None, None, None

    class Session:
        def __init__(self, *_):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            pass

        async def initialize(self):
            pass

        async def call_tool(self, *_a, **_k):
            raise ConnectionError("reply lost after dispatch")

    monkeypatch.setattr(mcp.client.streamable_http, "streamable_http_client", transport)
    monkeypatch.setattr(mcp, "ClientSession", Session)

    async def run():
        client = McpToolClient(Settings(tool_transport="mcp"), AsyncMock(), AsyncMock())
        client.executor.execute = AsyncMock(return_value={"reports": []})
        if replayed:
            result = await client.call(name, args)
            assert result["reports"] == []
            assert result["reportAvailability"]["answerKind"] == "empty"
            client.executor.execute.assert_awaited_once()
        else:
            with pytest.raises(ConnectionError):
                await client.call(name, args)
            client.executor.execute.assert_not_called()

    asyncio.run(run())
