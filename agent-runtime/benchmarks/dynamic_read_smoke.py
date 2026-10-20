"""Opt-in app smoke on an isolated test account already granted the MCP reads.

Set DYNAMIC_PROBE_USERNAME/PASSWORD privately. This creates test conversations,
does not change host grants, and records authored messages, replies and traces.
Answer markers are a smoke heuristic, not a semantic correctness evaluator.
"""

import argparse
import asyncio
import json
import os
import time
from pathlib import Path

import httpx


async def run(base_url, cases_path, output):
    if output.exists():
        raise ValueError("refusing to overwrite evidence")
    cases = json.loads(cases_path.read_text())["cases"]
    names = {name for case in cases for name in case["expectedTools"]}
    rows, session_id = [], None
    async with httpx.AsyncClient(
        base_url=base_url.rstrip("/") + "/", trust_env=False, timeout=120
    ) as client:
        login = await client.post(
            "auth/login",
            json={
                "username": os.environ["DYNAMIC_PROBE_USERNAME"],
                "password": os.environ["DYNAMIC_PROBE_PASSWORD"],
            },
        )
        login.raise_for_status()
        headers = {"Authorization": "Bearer " + login.json()["token"]}
        for case in cases:
            started = time.monotonic()
            try:
                response = await client.post(
                    "agent/chat",
                    headers=headers,
                    json={
                        "message": case["message"],
                        "sessionId": session_id if case.get("continuePreviousSession") else None,
                    },
                )
                response.raise_for_status()
                data = response.json()
                session_id = data["sessionId"]
                metrics = data.get("metrics") or {}
                calls = [c for c in metrics.get("capabilityCalls", []) if c["name"] in names]
                called = [c["name"] for c in calls if c["status"] == "ok"]
                pipeline = metrics.get("pipeline") or {}
                row = {
                    "id": case["id"],
                    "message": case["message"],
                    "reply": data["reply"],
                    "expectedTools": case["expectedTools"],
                    "calledTools": called,
                    "toolPass": sorted(called) == sorted(case["expectedTools"])
                    and all(c["status"] == "ok" for c in calls),
                    "answerMarkerPass": all(
                        any(marker in data["reply"] for marker in group)
                        for group in case["answerMarkers"]
                    ),
                    "trace": pipeline.get("executor", {}).get("decisions", []),
                    "loop": pipeline.get("loop"),
                    "semanticCalls": sum(
                        c.get("purpose") == "dynamic_read_action"
                        for c in metrics.get("modelCalls", [])
                    ),
                }
            except (httpx.HTTPError, ValueError, KeyError) as exc:
                session_id = None
                row = {"id": case["id"], "toolPass": False, "error": type(exc).__name__}
            row["elapsedSeconds"] = round(time.monotonic() - started, 2)
            rows.append(row)
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_text(
                json.dumps(
                    {"scope": "authored_development_smoke", "cases": rows},
                    ensure_ascii=False,
                    indent=2,
                )
                + "\n"
            )
            print(json.dumps({k: row[k] for k in ("id", "toolPass", "elapsedSeconds")}), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:5175/api")
    parser.add_argument(
        "--cases",
        type=Path,
        default=Path(__file__).with_name("cases") / "dynamic_reads_20261019.json",
    )
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    asyncio.run(run(args.base_url, args.cases, args.out))
