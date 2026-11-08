"""Frozen response examples for manual style comparison, not an LLM judge.

Real generation with fixed evidence, no business writes or automatic retries.
"""

import argparse
import asyncio
import hashlib
import json
import os
import shlex
import sys
import time
from pathlib import Path

CASES = [
    ("tired_guilt", "chat", "最近事情有点多，回家之后很累，但又觉得休息就是浪费时间。"),
    ("listen_only", "chat", "今天开会被领导当众批评，我挺难受的，不想听建议，只想说说。"),
    ("partial", "chat", "本来想出去散步五分钟，今天只走了一小段，还没做完。"),
    ("casual", "chat", "今天忙完了，终于有一点属于自己的时间。"),
    ("short", "chat", "今天有点累，只想听你两句简短的话。"),
    ("greeting", "chat", "你好呀。"),
    ("memory", "memory", "你记得我喜欢什么音乐、在哪里休息吗？请按已有记录回答。"),
    ("current_override", "chat", "我现在不想听音乐，只想安静待一会儿。"),
    ("knowledge", "knowledge", "什么是认知行为疗法？"),
]


def append_record(destination, row):
    with destination.open("a") as file:
        file.write(json.dumps(row, ensure_ascii=False) + "\n")


async def run(args):
    for path in [args.env, args.generation_env]:
        for line in Path(path).read_text().splitlines():
            k, sep, v = line.removeprefix("export ").partition("=")
            if sep and not k.startswith("#") and k not in {"HOME", "PATH", "CODEX_HOME"}:
                os.environ[k] = (shlex.split(v) or [""])[0]
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from psych_agent_runtime.config import Settings
    from benchmarks.runtime_fixture import make_runtime
    from psych_agent_runtime.ollama_client import OllamaClient
    from psych_agent_runtime.route_policy import plan_for_route
    from psych_agent_runtime.token_budget import ContextBudgetManager

    destination = Path(args.out)
    if destination.exists():
        raise SystemExit("Preserve earlier output; use another path")
    destination.parent.mkdir(parents=True, exist_ok=True)
    runtime = make_runtime()
    runtime.settings = Settings()
    runtime.context_budget = ContextBudgetManager(8192, 1200)
    client = OllamaClient(runtime.settings)
    hashes = []

    async def stream(messages, **kwargs):
        hashes.append(hashlib.sha256(json.dumps(messages, ensure_ascii=False).encode()).hexdigest())
        async for token in client.stream_chat(messages, **kwargs):
            yield token

    from types import SimpleNamespace

    runtime.ollama = SimpleNamespace(stream_chat=stream)
    runtime._emit = lambda *_: None
    print("Response prompt loaded", flush=True)
    doc = json.loads((Path(__file__).parents[1] / "knowledge/verified-starters.json").read_text())[
        "documents"
    ][0]
    try:
        for name, route, message in CASES:
            state = {
                "username": "style-fixture",
                "session_id": 9,
                "context": {"username": "style-fixture", "sessionId": 9},
                "message": message,
                "request_id": "style-" + name,
                "plan": plan_for_route(route, message, "frozen-style-probe").model_dump(),
                "tool_results": {},
            }
            if name in {"memory", "current_override"}:
                state["tool_results"]["memory_search"] = {
                    "status": "sufficient",
                    "purpose": "context" if name == "current_override" else "lookup",
                    "evidence": [
                        {
                            "role": "user",
                            "content": "我喜欢听轻音乐放松。",
                            "observedAt": "2026-10-12T09:00:00",
                            "timestampRole": "observation_time",
                            "sessionId": 1,
                        },
                        {
                            "role": "user",
                            "content": "我喜欢在安静的地方休息。",
                            "observedAt": "2026-10-12T09:02:00",
                            "timestampRole": "observation_time",
                            "sessionId": 1,
                        },
                    ],
                }
            if name == "knowledge":
                state["citations"] = [
                    {
                        "id": "frozen-nhs-cbt",
                        "document_id": doc["document_id"],
                        "content": doc["content"],
                        "source": doc["source"],
                        "category": doc["category"],
                        "rank": 1,
                    }
                ]
            started = time.perf_counter()
            reply = (await runtime._respond(state))["reply"]
            row = {
                "case": name,
                "message": message,
                "reply": reply,
                "replyChars": len(reply),
                "latencyMs": (time.perf_counter() - started) * 1000,
                "promptSha256": hashes[-1],
                "scope": "Fixed synthetic context; real generation; manual style review, not accuracy or clinical scoring",
            }
            await asyncio.to_thread(append_record, destination, row)
            print(name, len(reply), "characters", flush=True)
    finally:
        await client.close()


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--env", required=True)
    p.add_argument("--generation-env", required=True)
    p.add_argument("--out", required=True)
    asyncio.run(run(p.parse_args()))
