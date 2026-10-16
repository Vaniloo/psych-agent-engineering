"""Read-only HTTP scoring of a frozen lifecycle challenge, including abstentions."""

import argparse
import asyncio
import hashlib
import json
import os
import time
from pathlib import Path

from psych_agent_runtime.decision_contract import binary
from psych_agent_runtime.decision_router import LocalDecisions
from psych_agent_runtime.memory_lifecycle_contract import CONTRACT, INSTRUCTIONS
from psych_agent_runtime.qwen_memory_contract import INSTRUCTIONS as LEGACY_INSTRUCTIONS


def summary(rows):
    return {
        "rows": len(rows),
        "rawCorrect": sum(r["decision"]["choice"] == r["label"] for r in rows),
        "acceptedWrong": sum(
            r["decision"]["accepted"] and r["decision"]["choice"] != r["label"] for r in rows
        ),
        "usableCorrect": sum(
            r["decision"]["accepted"] and r["decision"]["choice"] == r["label"] for r in rows
        ),
        "abstained": sum(not r["decision"]["accepted"] for r in rows),
    }


async def run(args):
    if args.out.exists():
        raise ValueError("refuse to overwrite HTTP evaluation")
    raw = args.data.read_bytes()
    expected = json.loads(args.data.with_name("manifest.json").read_text())["casesSha256"]
    if hashlib.sha256(raw).hexdigest() != expected:
        raise ValueError("challenge checksum changed")
    cases = [
        {**row, "id": row.get("id") or f"legacy-row-{i}"}
        for i, row in enumerate(json.loads(line) for line in raw.decode().splitlines())
    ]
    instructions = LEGACY_INSTRUCTIONS if args.contract == "psych-memory-v4" else INSTRUCTIONS
    if any(
        len(row["expected"]) != 1 or not set(row["expected"]) <= instructions.keys()
        for row in cases
    ):
        raise ValueError("challenge task does not match selected contract")
    client = LocalDecisions(
        args.url,
        os.environ[args.key_env],
        45,
        args.contract,
        {k: binary(v) for k, v in instructions.items()},
        0.85,
    )
    rows = []
    started = time.monotonic()
    try:
        response = await client.client.get("/health")
        response.raise_for_status()
        health = response.json()
        if not health.get("ready") or health.get("contractVersion") != args.contract:
            raise ValueError("worker is not ready with selected contract")
        for task in instructions:
            subset = [r for r in cases if task in r["expected"]]
            for offset in range(0, len(subset), 16):
                batch = subset[offset : offset + 16]
                answers = await client.evaluate_many([r["state"] for r in batch], task)
                rows.extend(
                    {
                        "id": r["id"],
                        "task": task,
                        "label": r["expected"][task],
                        "decision": d,
                        "cohort": "new_edges"
                        if r["id"].startswith("lifecycle-edge-heldout-")
                        else "lifecycle_v1"
                        if r["id"].startswith("lifecycle-heldout-")
                        else "legacy",
                    }
                    for r, d in zip(batch, answers, strict=True)
                )
        response = await client.client.get("/health")
        response.raise_for_status()
        if response.json().get("artifact") != health.get("artifact"):
            raise ValueError("model artifact changed during scoring")
        report = {
            "scope": "Frozen authored development challenge through HTTP; no business writes, teacher API or production load test.",
            "health": health,
            "challengeSha256": expected,
            "elapsedSeconds": round(time.monotonic() - started, 3),
            "all": summary(rows),
            "byTask": {t: summary([r for r in rows if r["task"] == t]) for t in instructions},
            "cohorts": {
                c: summary([r for r in rows if r["cohort"] == c])
                for c in ("legacy", "lifecycle_v1", "new_edges")
            },
            "cases": rows,
        }
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
        print(
            json.dumps(
                {"model": health["model"], "cohorts": report["cohorts"]}, ensure_ascii=False
            ),
            flush=True,
        )
    finally:
        await client.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", required=True)
    parser.add_argument("--contract", default=CONTRACT, choices=[CONTRACT, "psych-memory-v4"])
    parser.add_argument("--key-env", default="MEMORY_LIFECYCLE_SHADOW_KEY")
    parser.add_argument("--data", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    asyncio.run(run(parser.parse_args()))
