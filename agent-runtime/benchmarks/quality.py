"""Frozen synthetic evidence tasks, separate from training and provider grading.

Tests current read paths against source IDs, without user business writes. Run the
same cases before/after changes; credentials are loaded privately before imports.
"""

import argparse
import asyncio
import json
import os
import shlex
import sys
import time
from pathlib import Path

FACTS = [
    "我喜欢听爵士乐休息。",
    "我喜欢在小区花园休息。",
    "我喜欢读推理小说放松。",
    "我不喜欢喝咖啡，因为会影响睡眠。",
    "我的姐姐喜欢听摇滚乐。",
    "我的同事喜欢在办公室休息。",
    "我周末通常骑自行车放松。",
    "我以前喜欢跑步，现在膝盖不舒服，改为慢慢散步。",
    "昨天我写了两句感受，还没写完三句。",
    "今天我喝了一杯温水。",
    "我一般在晚上十点半准备睡觉。",
    "My favorite relaxing music is piano music.",
    "I prefer resting on the balcony.",
    "My colleague enjoys resting at the cafe.",
]

MEMORY_CASES = [
    ("music", "你记得我喜欢听什么音乐吗？", [0], [4]),
    ("place", "我以前说过喜欢在哪里休息？", [1], [5]),
    ("compound", "我之前提到喜欢听什么、在哪里休息？", [0, 1], [4, 5]),
    ("compound_reworded", "请回顾我说过的音乐偏好和休息地点。", [0, 1], [4, 5]),
    ("reading", "我喜欢读什么书来放松？只根据以前记录。", [2], []),
    ("negative_preference", "我对咖啡的看法是什么？", [3], []),
    ("weekend", "我周末通常怎么放松？", [6], []),
    ("changed_preference", "我现在还喜欢跑步吗，后来有什么变化？", [7], []),
    ("partial", "我昨天写三句感受的步骤做完了吗？", [8], []),
    ("drink", "今天我喝了什么？", [9], []),
    ("bedtime", "我以前说过几点准备睡觉？", [10], []),
    ("english_compound", "What music do I like and where do I prefer resting?", [11, 12], [13]),
]

ROUTE_CASES = [
    ("self_recall", "我之前提到喜欢听什么、在哪里休息？", True),
    ("self_recall_alt", "请回顾我说过的音乐偏好和休息地点。", True),
    ("explicit_record", "根据之前的记录，告诉我喜欢读哪类书。", True),
    ("english", "Do you remember what I said about my hobbies?", True),
    ("knowledge", "什么是记忆，为什么会忘记？", False),
    ("negation", "不要查我之前的记录，只解释什么是CBT。", False),
    ("quote", "请解释这句话：‘你记得我喜欢什么吗？’", False),
    ("other_person", "朋友问我‘你记得我喜欢什么吗’，如何回复？", False),
    ("ordinary", "我今天在花园休息，很舒服。", False),
    ("mixed_report", "查看我的报告，再回顾我之前提到的休息偏好。", True),
]


async def run(args):
    for line in Path(args.env).read_text().splitlines():
        key, sep, value = line.removeprefix("export ").partition("=")
        if sep and not key.startswith("#") and key not in {"HOME", "PATH", "CODEX_HOME"}:
            os.environ[key] = (shlex.split(value) or [""])[0]
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

    from psych_agent_runtime.config import Settings
    from psych_agent_runtime.memory import MemoryService
    from psych_agent_runtime.deepseek_client import DeepSeekClient
    from psych_agent_runtime.planner import AgentPlanner

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    db = out / (args.label + "-evidence.sqlite3")
    if db.exists():
        raise SystemExit("Existing evaluation index; choose another label instead of overwriting")
    from dataclasses import replace

    settings = replace(Settings(), memory_store_path=str(db))
    service = MemoryService(settings)
    client = DeepSeekClient(settings) if settings.deepseek_api_key else None
    planner = AgentPlanner(client)
    rows = []
    try:
        for owner, repeated in [("clean", False), ("repeated_questions", True)]:
            for i, fact in enumerate(FACTS):
                service.store.ingest(
                    {
                        "username": owner,
                        "sessionId": 1,
                        "requestId": f"fact-{i}",
                        "userMessage": fact,
                        "assistantReply": "",
                        "createdAt": f"2026-10-01T10:{i:02d}:00",
                    }
                )
            if repeated:
                for i in range(30):
                    service.store.ingest(
                        {
                            "username": owner,
                            "sessionId": 2,
                            "requestId": f"query-{i}",
                            "userMessage": MEMORY_CASES[2][1],
                            "assistantReply": "",
                            "createdAt": f"2026-10-12T10:{i:02d}:00",
                        }
                    )
            for case, query, expected, forbidden in MEMORY_CASES:
                started = time.perf_counter()
                result = await service.retrieve(owner, query)
                sources = {r["source"] for r in result["evidence"]}
                row = {
                    "kind": "memory",
                    "ownerFixture": owner,
                    "case": case,
                    "query": query,
                    "expected": expected,
                    "forbidden": forbidden,
                    "covered": [i for i in expected if f"fact-{i}" in sources],
                    "wrongPerson": [i for i in forbidden if f"fact-{i}" in sources],
                    "latencyMs": round((time.perf_counter() - started) * 1000, 2),
                    "result": result,
                }
                row["complete"] = row["covered"] == expected and not row["wrongPerson"]
                rows.append(row)
                print(owner, case, "complete" if row["complete"] else "partial", flush=True)
        for case, message, expected in ROUTE_CASES:
            plan = await planner.plan(message)
            rows.append(
                {
                    "kind": "route",
                    "provider": plan.routing["provider"],
                    "case": case,
                    "message": message,
                    "expectedMemory": expected,
                    "plan": plan.model_dump(),
                    "memoryAllowed": "memory_search" in plan.allowed_tools,
                    "memoryRequired": "memory_search" in plan.required_tools,
                }
            )
    finally:
        if client is not None:
            await client.close()
        await service.close()

    memory = [r for r in rows if r["kind"] == "memory"]
    payload = {
        "label": args.label,
        "scope": "Frozen synthetic fixture; retrieval source coverage, not clinical or response accuracy",
        "memoryCases": len(memory),
        "complete": sum(r["complete"] for r in memory),
        "wrongPersonCases": sum(bool(r["wrongPerson"]) for r in memory),
        "rows": rows,
    }
    (out / (args.label + ".json")).write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    )
    print(json.dumps({k: v for k, v in payload.items() if k != "rows"}), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--env", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--label", required=True)
    asyncio.run(run(parser.parse_args()))
