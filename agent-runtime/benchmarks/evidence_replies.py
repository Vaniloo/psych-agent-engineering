"""Render the held-out retrieval evidence through the real response prompt.

No business persistence, role training or teacher scoring. Replies are retained
for manual review, including ambiguous direction and absent-source cases.
"""

import argparse
import asyncio
import json
import os
import shlex
import sys
from pathlib import Path


def append_record(path, record):
    with path.open("a") as file:
        file.write(json.dumps(record, ensure_ascii=False) + "\n")


async def run(args):
    for path in [args.env, args.generation_env]:
        for line in Path(path).read_text().splitlines():
            key, sep, value = line.removeprefix("export ").partition("=")
            if sep and not key.startswith("#") and key not in {"HOME", "PATH", "CODEX_HOME"}:
                os.environ[key] = (shlex.split(value) or [""])[0]
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from psych_agent_runtime.config import Settings
    from psych_agent_runtime.graph import AgentGraphRuntime
    from psych_agent_runtime.ollama_client import OllamaClient
    from psych_agent_runtime.route_policy import plan_for_route
    from psych_agent_runtime.token_budget import ContextBudgetManager

    source = json.loads(Path(args.source).read_text())
    rows = source["rows"]
    out = Path(args.out)
    if out.exists():
        raise SystemExit("Preserve previous replies; choose another output")
    runtime = AgentGraphRuntime.__new__(AgentGraphRuntime)
    runtime.settings = Settings()
    runtime.context_budget = ContextBudgetManager(8192, 1200)
    runtime.ollama = OllamaClient(runtime.settings)
    runtime._emit = lambda *_: None
    try:
        for row in rows:
            state = {
                "username": "synthetic-holdout",
                "session_id": 1,
                "context": {"username": "synthetic-holdout", "sessionId": 1},
                "request_id": "holdout-reply-" + row["case"],
                "message": row["query"] + " 请只按本轮记录简短回答，缺少依据就直说。",
                "plan": plan_for_route(
                    "memory", row["query"], "frozen-evidence-review"
                ).model_dump(),
                "tool_results": {"memory_search": row["result"]},
            }
            result = await runtime._respond(state)
            record = {
                "case": row["case"],
                "query": row["query"],
                "evidence": row["result"]["evidence"],
                "retrievalStatus": row["result"]["status"],
                "reply": result["reply"],
                "scope": "Real generation prompt on synthetic frozen evidence; no business write or automatic judge",
            }
            await asyncio.to_thread(append_record, out, record)
            print(row["case"], "reply saved", flush=True)
    finally:
        await runtime.ollama.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--env", required=True)
    parser.add_argument("--generation-env", required=True)
    parser.add_argument("--source", required=True)
    parser.add_argument("--out", required=True)
    asyncio.run(run(parser.parse_args()))
