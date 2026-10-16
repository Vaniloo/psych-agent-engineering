"""One synthetic shadow sequence against a real lifecycle worker; never changes user memory."""

import argparse
import asyncio
import json
import os
import time
from pathlib import Path

from psych_agent_runtime.memory_ledger import MemoryLedger, proposal
from psych_agent_runtime.memory_lifecycle_contract import CONTRACT, LIFECYCLE_INSTRUCTIONS
from psych_agent_runtime.memory_shadow import MemoryLifecycleShadow


async def run(args):
    if args.out.exists():
        raise ValueError("refuse to overwrite a shadow diagnostic")
    args.out.mkdir(parents=True)
    ledger = MemoryLedger(args.out / "synthetic-ledger.sqlite3")
    # Seed facts are authored fixture state, not model predictions or real users.
    seed = {k: {"choice": "no", "accepted": True} for k in LIFECYCLE_INSTRUCTIONS}
    seed.update({k: {"choice": "yes", "accepted": True} for k in ("write_worthy", "grounded")})

    def job(text, number, owner="synthetic-lifecycle"):
        return {
            "username": owner,
            "sessionId": 1,
            "requestId": f"fixture-{number}",
            "userMessage": text,
            "createdAt": "2026-10-15T12:00:00+08:00",
        }

    for i, text in enumerate(("我平时喜欢听轻音乐。", "我唯一一只兔子叫芝麻团。")):
        ledger.apply(proposal(job(text, i), 0, len(text), scope="stable"), seed, enabled=True)
    ledger.apply(
        proposal(
            job("我喜欢重金属音乐。", "other", "other-account"),
            0,
            len("我喜欢重金属音乐。"),
            scope="stable",
        ),
        seed,
        enabled=True,
    )
    before = ledger.candidates("synthetic-lifecycle")
    shadow = MemoryLifecycleShadow(ledger, args.url, os.environ[args.key_env], timeout=10)
    cases = [
        ("repeat", "我平时喜欢听轻音乐。", "support"),
        ("temporary", "今天我不想听轻音乐。", "skip"),
        ("explicit_change", "更正：我平时喜欢听的是民谣，不是轻音乐。", "replace"),
        ("unresolved_name", "我唯一一只兔子叫海盐包。", "keep_separate"),
        ("new_preference", "我平时最喜欢去森林步道散步。", "add"),
        ("query_only", "我家的兔子叫什么来着？", None),
        ("optout", "不要长期记住：我喜欢吃辣。", None),
        ("today_mood", "我今天有点高兴。", "skip"),
    ]
    rows = []
    try:
        for i, (name, text, expected) in enumerate(cases, 10):
            started = time.monotonic()
            result = await shadow.inspect(job(text, i))
            actions = [p["action"] for p in result["proposals"]]
            passed = expected in actions if expected else not actions
            rows.append(
                {
                    "case": name,
                    "message": text,
                    "expectedAction": expected,
                    "actionMatched": passed,
                    "latencyMs": round((time.monotonic() - started) * 1000),
                    "result": result,
                }
            )
            print(name, actions, "expected", expected, "matched", passed, flush=True)
        after = ledger.candidates("synthetic-lifecycle")
        if before != after:
            raise RuntimeError("shadow path modified seeded facts")
        payload = {
            "contract": CONTRACT,
            "cases": len(rows),
            "matched": sum(r["actionMatched"] for r in rows),
            "formalMemoryWrites": 0,
            "seededFactsUnchanged": True,
            "scope": "Authored synthetic shadow cases, not independent evaluation or production rollout.",
            "rows": rows,
        }
        (args.out / "results.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2))
    finally:
        await shadow.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", required=True)
    parser.add_argument("--key-env", default="QWEN_MEMORY_API_KEY")
    parser.add_argument("--out", type=Path, required=True)
    asyncio.run(run(parser.parse_args()))
