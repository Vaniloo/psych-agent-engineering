"""Real memory workers and generation on frozen cross-session fixtures.

Uses a disposable owned index; no business writes, teacher API or retries.
"""

import argparse
import asyncio
import json
import os
import shlex
import sys
import time
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

CASES = [
    (
        "relax",
        "我今天有点累，想找一种适合我的放松方式。请简短回应。",
        ["我喜欢听轻音乐放松。", "我喜欢在安静的花园散步。", "我的朋友喜欢玩桌游。"],
    ),
    (
        "interview",
        "上次那场面试还是让我有点紧张，想接着聊聊。请简短回应。",
        ["我上周准备设计师面试，一想到自我介绍就紧张。", "我周末在家做了蛋糕。"],
    ),
    (
        "latest_override",
        "我现在不想听音乐，只想安静待一会儿。请简短回应。",
        ["我以前喜欢听爵士乐放松。", "我以前喜欢去花园散步。"],
    ),
    ("unrelated", "我正在准备面试，有点紧张。请简短回应。", ["我前天买了一把蓝色雨伞。"]),
    ("optout", "不要查以前的记录，我今天有点累，只聊这次。请简短回应。", ["我喜欢听轻音乐放松。"]),
    ("current_only", "我今天有点累，想轻松聊聊。请简短回应。", []),
]


async def run(args):
    for path in [args.env, args.generation_env]:
        for line in Path(path).read_text().splitlines():
            k, sep, v = line.removeprefix("export ").partition("=")
            if sep and not k.startswith("#") and k not in {"HOME", "PATH", "CODEX_HOME"}:
                os.environ[k] = (shlex.split(v) or [""])[0]
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from benchmarks.loop_probe import run_read_loop
    from psych_agent_runtime.capabilities import CapabilityExecutor
    from psych_agent_runtime.config import Settings
    from unittest.mock import AsyncMock
    from benchmarks.runtime_fixture import make_runtime
    from psych_agent_runtime.memory import MemoryService
    from psych_agent_runtime.ollama_client import OllamaClient
    from psych_agent_runtime.route_policy import plan_for_route
    from psych_agent_runtime.token_budget import ContextBudgetManager
    from psych_agent_runtime.tool_controller import ToolController

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    db = out / "fixtures.sqlite3"
    dest = out / "worker-replies.jsonl"
    if db.exists() or dest.exists():
        raise SystemExit("Preserve prior results; choose a new directory")
    s = replace(Settings(), memory_store_path=str(db), agent_loop_refinement_enabled=False)
    memory = MemoryService(s)
    executor = CapabilityExecutor(None, None, memory)
    runtime = make_runtime()
    runtime.settings = s
    runtime.memory = memory
    runtime.tools = SimpleNamespace(catalog=SimpleNamespace(refresh=AsyncMock()), call=executor.execute)
    runtime.tool_controller = ToolController(s)
    runtime.context_budget = ContextBudgetManager(8192, 1200)
    runtime.ollama = OllamaClient(s)
    runtime._emit = lambda *_: None
    try:
        for name, message, facts in CASES:
            owner = "context-fixture-" + name
            for i, content in enumerate(facts + ["当前会话专用原话，不属于旧背景。"]):
                memory.store.ingest(
                    {
                        "username": owner,
                        "sessionId": 9 if i == len(facts) else 1,
                        "requestId": f"{name}-{i}",
                        "userMessage": content,
                        "assistantReply": "",
                        "createdAt": f"2026-10-13T10:{i:02d}:00",
                    }
                )
            memory.store.ingest(
                {
                    "username": "foreign",
                    "sessionId": 1,
                    "requestId": "foreign",
                    "userMessage": "别人最喜欢登山。",
                    "assistantReply": "",
                    "createdAt": "2026-10-13T10:00:00",
                }
            )
            state = {
                "username": owner,
                "session_id": 9,
                "context": {"username": owner, "sessionId": 9},
                "message": message,
                "request_id": "probe-" + name,
                "plan": plan_for_route("chat", message, "frozen-background-probe").model_dump(),
            }
            start = time.perf_counter()
            state.update(await run_read_loop(runtime, state))
            tool_ms = (time.perf_counter() - start) * 1000
            state.update(await runtime._respond(state))
            evidence = (state["tool_results"].get("memory_search") or {}).get("evidence", [])
            assert all(e["sessionId"] != 9 and e["source"].startswith(name + "-") for e in evidence)
            row = {
                "case": name,
                "message": message,
                "tools": list(state["tool_results"]),
                "memory": state["tool_results"].get("memory_search"),
                "reply": state["reply"],
                "toolMs": tool_ms,
                "latencyMs": (time.perf_counter() - start) * 1000,
                "scope": "Frozen synthetic worker and response test, no business persistence; manual review required",
            }
            with dest.open("a") as f:
                f.write(json.dumps(row, ensure_ascii=False) + "\n")
            print(name, len(evidence), "sources", flush=True)
    finally:
        await memory.close()
        await runtime.tool_controller.close()
        await runtime.ollama.close()


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--env", required=True)
    p.add_argument("--generation-env", required=True)
    p.add_argument("--out", required=True)
    asyncio.run(run(p.parse_args()))
