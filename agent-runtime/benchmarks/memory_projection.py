"""Sequential synthetic projection through a real controller, with durable undo checks."""

import argparse
import asyncio
import json
import os
from pathlib import Path

from psych_agent_runtime.memory_ledger import MemoryLedger
from psych_agent_runtime.memory_projection import MemoryProjection
from psych_agent_runtime.memory_shadow import POLICY_VERSION, MemoryLifecycleShadow


async def run(args):
    if args.out.exists():
        raise ValueError("refuse to overwrite projection diagnostic")
    args.out.mkdir(parents=True)
    ledger = MemoryLedger(args.out / "synthetic-ledger.sqlite3")
    owner = "synthetic-projection"
    writer = MemoryProjection(ledger, {owner})
    shadow = MemoryLifecycleShadow(ledger, args.url, os.environ[args.key_env], timeout=15)
    cases = [
        ("music", "我平时喜欢听轻音乐。", ["add"], 1),
        ("pet", "我唯一一只兔子叫芝麻团。", ["add"], 1),
        ("repeat", "我平时喜欢听轻音乐。", ["support"], 1),
        ("temporary", "今天我不想听轻音乐。", ["skip"], 0),
        ("correction", "更正：我平时喜欢听的是民谣，不是轻音乐。", ["replace"], 1),
        ("coexist", "我平时最喜欢去森林步道散步。", ["add"], 1),
        ("atomic", "我的职业是教师，我今天有点开心。", ["add", "skip"], 1),
        ("unresolved", "我唯一一只兔子叫海盐包。", ["keep_separate"], 0),
        ("optout", "不要长期记住：我喜欢吃辣。", [], 0),
        ("question", "我的兔子叫什么来着？", [], 0),
    ]
    rows = []
    undo = {"exercised": False}
    original_music = None
    pet = None
    try:
        for i, (name, text, expected, writes) in enumerate(cases):
            job = {
                "username": owner,
                "sessionId": 1,
                "requestId": f"projection-{i}",
                "userMessage": text,
                "createdAt": "2026-10-16T12:00:00+08:00",
            }
            preview = await shadow.inspect(job)
            actions = [p["action"] for p in preview["proposals"]]
            applied = writer.project(job, preview)
            count = sum(r.get("applied") is True for r in applied)
            row = {
                "case": name,
                "message": text,
                "expectedActions": expected,
                "expectedExperimentalWrites": writes,
                "matched": actions == expected and count == writes,
                "preview": preview,
                "projection": applied,
            }
            rows.append(row)
            print(
                name, actions, "experimental writes", count, "matched", row["matched"], flush=True
            )
            if name == "music" and count:
                original_music = ledger.candidates(owner)[0]
            if name == "pet" and count:
                pet = next(c for c in ledger.candidates(owner) if "兔子" in c["content"])
            if name == "correction" and count and original_music:
                change = next(r for r in applied if r.get("applied"))
                # Reopen the database: undo must not depend on in-memory state.
                reopened = MemoryLedger(ledger.path)
                reopened.undo(owner, change["changeId"], enabled=True)
                assert reopened.get(owner, original_music["id"])["status"] == "active"
                assert (
                    reopened.get(owner, original_music["id"])["version"] > original_music["version"]
                )
                assert pet is None or reopened.get(owner, pet["id"]) == pet
                assert (
                    reopened.undo(owner, change["changeId"], enabled=True)["action"]
                    == "undo_replay"
                )
                undo = {
                    "exercised": True,
                    "restoredMusic": True,
                    "petUnchanged": True,
                    "idempotent": True,
                }
        if pet:
            assert ledger.get(owner, pet["id"]) == pet, "unrelated pet was modified"
        assert ledger.candidates("other-account") == []
        report = {
            "scope": "Sequential authored synthetic development diagnostic; writes only the newly created experimental SQLite file, never business memory.",
            "model": next((p["model"] for r in rows for p in r["preview"]["proposals"]), None),
            "policyVersion": POLICY_VERSION,
            "cases": len(rows),
            "matched": sum(r["matched"] for r in rows),
            "formalMemoryWrites": 0,
            "experimentalWrites": sum(
                p.get("applied") is True for r in rows for p in r["projection"]
            ),
            "activeExperimentalFacts": len(ledger.candidates(owner, limit=200)),
            "undo": undo,
            "rows": rows,
        }
        (args.out / "results.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n"
        )
    finally:
        await shadow.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", required=True)
    parser.add_argument("--key-env", default="MEMORY_LIFECYCLE_SHADOW_KEY")
    parser.add_argument("--out", type=Path, required=True)
    asyncio.run(run(parser.parse_args()))
