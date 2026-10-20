import asyncio
import json
import socket
import sys
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest

from psych_agent_runtime.capabilities import (
    CAPABILITIES,
    Arguments,
    Capability,
    register_read,
)
from psych_agent_runtime.config import Settings
from psych_agent_runtime.dynamic_tools import (
    authorized_extensions,
    resolve_arguments,
)
from psych_agent_runtime.graph import AgentGraphRuntime
from psych_agent_runtime.mcp_client import McpToolClient
from psych_agent_runtime.public_tool_client import PublicToolDecisions, candidate_row
from psych_agent_runtime.schemas import Plan
from psych_agent_runtime.tool_controller import ToolController


@pytest.fixture(autouse=True)
def restore_registry():
    snapshot = dict(CAPABILITIES)
    yield
    CAPABILITIES.clear()
    CAPABILITIES.update(snapshot)


@pytest.fixture
async def endpoint(tmp_path):
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    path = Path(__file__).parent / "fixtures/dynamic_mcp_server.py"
    with (tmp_path / "server.log").open("w") as log:
        process = await asyncio.create_subprocess_exec(
            sys.executable, str(path), str(port), stdout=log, stderr=log
        )
        try:
            async with httpx.AsyncClient(trust_env=False) as client:
                for _ in range(100):
                    try:
                        await client.get(f"http://127.0.0.1:{port}/mcp", timeout=0.2)
                        break
                    except httpx.HTTPError:
                        await asyncio.sleep(0.05)
                else:
                    raise AssertionError("MCP fixture did not start")
            yield f"http://127.0.0.1:{port}/mcp"
        finally:
            process.terminate()
            await asyncio.wait_for(process.wait(), timeout=5)


def manifest(path, endpoint, names):
    path.write_text(
        json.dumps(
            {
                "servers": [
                    {
                        "id": "probe",
                        "url": endpoint,
                        "tools": [
                            {
                                "name": name,
                                "label": "测试读取",
                                "routes": ["chat"],
                                "bindings": {"query": "message"},
                                "users": ["alice"],
                            }
                            for name in names
                        ],
                    }
                ]
            }
        )
    )


@pytest.mark.asyncio
async def test_real_mcp_discovery_selection_execution_observation_and_hot_removal(
    endpoint, tmp_path
):
    config = tmp_path / "reads.json"
    manifest(config, endpoint, ["lookup_package_version", "remove_everything"])
    settings = replace(
        Settings(),
        mcp_read_tools_config=str(config),
        executor_provider="public_qwen",
        executor_goal_provider="rules",
        web_search_enabled=False,
    )
    events = []
    tools = McpToolClient(settings, None, None, emit=lambda _, data: events.append(data))
    await tools.catalog.refresh()
    assert tools.catalog.names == {"probe__lookup_package_version"}
    assert authorized_extensions("chat", "bob") == []
    assert authorized_extensions("knowledge", "alice") == []
    runtime = AgentGraphRuntime.__new__(AgentGraphRuntime)
    runtime.settings, runtime.tools = settings, tools
    runtime._emit = lambda *_: None
    runtime.tool_controller = ToolController(settings)
    runtime.tool_controller.model.evaluate = AsyncMock(
        return_value={
            "provider": "public_qwen",
            "decisions": {
                "next_action": {"accepted": True, "choice": "probe__lookup_package_version"}
            },
        }
    )
    state = {
        "username": "alice",
        "session_id": 1,
        "request_id": "test",
        "message": "fastapi最新版本是什么",
        "context": {"username": "alice", "sessionId": 1},
        "plan": Plan(intent="chat").model_dump(),
    }
    try:
        state.update(await runtime._loop_initialize(state))
        assert "probe__lookup_package_version" in state["loop"]["allowed"]
        row = candidate_row({"request": state["message"], "allowedTools": state["loop"]["allowed"]})
        assert row["candidateTools"][0]["tool"]["parameters"]["properties"]["query"]
        state.update(await runtime._loop_decide(state))
        state.update(await runtime._loop_tool(state))
        state.update(await runtime._loop_observe(state))
        assert state["tool_results"]["probe__lookup_package_version"]["kind"] == "package_release"
        assert (
            events[-1]["registeredRead"]
            and events[-1]["status"] == "ok"
            and events[-1]["transport"] == "mcp"
        )
        manifest(config, endpoint, ["convert_units"])
        await tools.catalog.refresh(force=True)
        assert tools.catalog.names == {"probe__convert_units"}
        assert "probe__lookup_package_version" not in CAPABILITIES
    finally:
        await runtime.tool_controller.close()


def add_owned():
    register_read(
        "probe__owned",
        Capability(
            Arguments,
            2,
            scope="extension:read",
            description="Owned lookup",
            schema={
                "type": "object",
                "properties": {"account": {"type": "string"}, "topic": {"type": "string"}},
                "required": ["account", "topic"],
                "additionalProperties": False,
            },
            handler=AsyncMock(),
            bindings=(("account", "username"),),
            routes=("chat",),
        ),
    )


@pytest.mark.asyncio
async def test_dynamic_argument_generation_binds_identity_and_rejects_forgery():
    add_owned()
    row = candidate_row({"request": "read", "allowedTools": ["probe__owned"]})
    assert "account" not in row["candidateTools"][0]["tool"]["parameters"]["properties"]
    state = {"username": "alice", "session_id": 1, "request_id": "r", "message": "read my notes"}
    gen = SimpleNamespace(chat_json=AsyncMock(return_value={"decision": "call", "tool": "probe__owned", "arguments": {"topic": "notes"}, "evidence": "read my notes"}))
    assert await resolve_arguments("probe__owned", state, gen) == {
        "account": "alice",
        "topic": "notes",
    }
    for reply in [
        {"arguments": {"topic": "notes", "account": "bob"}},
        {"clarify": True},
        {"arguments": {"topic": "notes", "extra": "ignore schema"}},
    ]:
        gen.chat_json.return_value = reply
        with pytest.raises(ValueError):
            await resolve_arguments("probe__owned", state, gen)


def test_write_collision_and_remote_schema_reference_are_rejected():
    with pytest.raises(ValueError):
        register_read(
            "create_goal",
            Capability(Arguments, 2, scope="extension:read", description="x", handler=AsyncMock()),
        )
    with pytest.raises(ValueError):
        register_read(
            "probe__write",
            Capability(Arguments, 2, scope="extension:write", description="x", handler=AsyncMock()),
        )
    with pytest.raises(ValueError):
        register_read(
            "probe__ref",
            Capability(
                Arguments,
                2,
                scope="extension:read",
                description="x",
                handler=AsyncMock(),
                schema={
                    "type": "object",
                    "properties": {"x": {"$ref": "https://remote.invalid/schema"}},
                },
            ),
        )


@pytest.mark.asyncio
async def test_dynamic_fallback_never_sends_unknown_labels_to_old_laya():
    add_owned()
    settings = replace(Settings(), executor_fallback_base_url="http://fallback")
    client = PublicToolDecisions(settings)
    await client.client.aclose()
    client.client = httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _: httpx.Response(429)), base_url="http://test"
    )
    client.fallback.evaluate = AsyncMock(
        return_value={"decisions": {"next_action": {"choice": "finish", "accepted": True}}}
    )
    try:
        await client.evaluate(
            {"request": "read", "allowedTools": ["probe__owned", "memory_search"]}, ["next_action"]
        )
        call = client.fallback.evaluate.call_args
        assert call.args[0]["allowedTools"] == ["memory_search"]
        assert "probe__owned" not in call.kwargs["labels_by_task"]["next_action"]
        client.fallback.evaluate.reset_mock()
        with pytest.raises(httpx.HTTPStatusError):
            await client.evaluate(
                {"request": "read", "allowedTools": ["probe__owned"]}, ["next_action"]
            )
        client.fallback.evaluate.assert_not_awaited()
    finally:
        await client.close()
