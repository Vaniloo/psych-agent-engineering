"""Opt-in live scorer smoke, with a synthetic local MCP server and no business writes.

Run with the private Runtime environment loaded, --out /private/report.json.
This tests integration on two new names, not broad unseen-tool accuracy.
"""

import argparse
import asyncio
import json
import socket
import sys
import tempfile
import time
from dataclasses import replace
from pathlib import Path

import httpx

from psych_agent_runtime.capabilities import CAPABILITIES
from psych_agent_runtime.config import Settings
from psych_agent_runtime.graph import AgentGraphRuntime
from psych_agent_runtime.mcp_client import McpToolClient
from psych_agent_runtime.schemas import Plan
from psych_agent_runtime.tool_controller import ToolController


async def run(output):
    if output.exists():
        raise ValueError("refusing to overwrite report")
    snapshot = dict(CAPABILITIES)
    with tempfile.TemporaryDirectory() as directory, socket.socket() as sock:
        folder = Path(directory)
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
        sock.close()
        fixture = Path(__file__).resolve().parents[1] / "tests/fixtures/dynamic_mcp_server.py"
        log = (folder / "server.log").open("w")
        process = await asyncio.create_subprocess_exec(
            sys.executable, str(fixture), str(port), stdout=log, stderr=log
        )
        config = folder / "reads.json"
        config.write_text(
            json.dumps(
                {
                    "servers": [
                        {
                            "id": "novel",
                            "url": f"http://127.0.0.1:{port}/mcp",
                            "tools": [
                                {
                                    "name": name,
                                    "label": "测试工具",
                                    "routes": ["chat"],
                                    "bindings": {"query": "message"},
                                    "users": ["integration-probe"],
                                }
                                for name in ["lookup_package_version", "convert_units"]
                            ],
                        }
                    ]
                }
            )
        )
        settings = replace(
            Settings(),
            mcp_read_tools_config=str(config),
            web_search_enabled=False,
            agent_loop_max_tool_calls=1,
            agent_loop_refinement_enabled=False,
        )
        if settings.executor_provider != "public_qwen":
            raise ValueError("live public_qwen must be configured explicitly")
        runtime = AgentGraphRuntime.__new__(AgentGraphRuntime)
        runtime.settings = settings
        runtime._emit = lambda *_: None
        events = []
        runtime.tools = McpToolClient(settings, None, None, emit=lambda _, d: events.append(d))
        runtime.tool_controller = ToolController(settings)
        results = []
        try:
            async with httpx.AsyncClient(trust_env=False) as client:
                for _ in range(100):
                    try:
                        await client.get(f"http://127.0.0.1:{port}/mcp", timeout=0.2)
                        break
                    except httpx.HTTPError:
                        await asyncio.sleep(0.05)
            await runtime.tools.catalog.refresh()
            for query, expected in [
                ("帮我查一下 fastapi 包目前最新的版本", "novel__lookup_package_version"),
                ("请调用单位换算工具，把36摄氏度转换成华氏度", "novel__convert_units"),
            ]:
                started = time.monotonic()
                state = {
                    "username": "integration-probe",
                    "session_id": 1,
                    "request_id": "probe",
                    "message": query,
                    "context": {"username": "integration-probe", "sessionId": 1},
                    "plan": Plan(intent="chat", max_tool_calls=1).model_dump(),
                }
                state.update(await runtime._loop_initialize(state))
                state.update(await runtime._loop_decide(state))
                selected = state["loop"]["next"]["tool"]
                if selected not in {"finish", "clarify"}:
                    state.update(await runtime._loop_tool(state))
                    state.update(await runtime._loop_observe(state))
                result = state.get("tool_results", {}).get(expected, {})
                results.append(
                    {
                        "query": query,
                        "expected": expected,
                        "selected": selected,
                        "correct": selected == expected,
                        "executedFixture": result.get("fixture") is True,
                        "trace": state["loop"]["trace"],
                        "durationMs": round((time.monotonic() - started) * 1000, 2),
                    }
                )
            report = {
                "scope": "two_synthetic_tools_real_mcp_and_real_scorer_not_generalization_eval",
                "adapterSha256": settings.executor_adapter_sha256,
                "writes": 0,
                "catalog": runtime.tools.catalog.info(),
                "cases": results,
                "toolActivities": [
                    {
                        k: e[k]
                        for k in ("name", "label", "status", "registeredRead", "transport")
                        if k in e
                    }
                    for e in events
                ],
            }
            output.parent.mkdir(parents=True, exist_ok=True)
            output.touch(mode=0o600)
            output.write_text(json.dumps(report, ensure_ascii=False, indent=2))
            print(
                json.dumps(
                    {
                        "correct": sum(r["correct"] for r in results),
                        "executed": sum(r["executedFixture"] for r in results),
                        "cases": len(results),
                        "writes": 0,
                    }
                )
            )
        finally:
            await runtime.tool_controller.close()
            CAPABILITIES.clear()
            CAPABILITIES.update(snapshot)
            process.terminate()
            await asyncio.wait_for(process.wait(), timeout=5)
            log.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", required=True, type=Path)
    asyncio.run(run(parser.parse_args().out))
