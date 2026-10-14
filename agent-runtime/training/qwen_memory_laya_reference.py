"""Current Memory worker reference on the seven-task frozen challenge; read-only."""

import argparse
import asyncio
import hashlib
import json
import os
import shlex
from collections import defaultdict
from pathlib import Path


async def run(args):
    for line in args.env.read_text().splitlines():
        key, sep, value = line.removeprefix("export ").partition("=")
        if sep and key in {"MEMORY_BASE_URL", "MEMORY_API_KEY"}:
            parts = shlex.split(value)
            os.environ[key] = parts[0] if parts else ""
    from psych_agent_runtime.config import Settings
    from psych_agent_runtime.decision_contract import MEMORY_EVIDENCE_CONTRACT, profile_contract
    from psych_agent_runtime.decision_router import LocalDecisions
    from psych_agent_runtime.qwen_memory_contract import normalize

    if args.out.exists():
        raise ValueError("refuse to overwrite reference")
    raw = args.challenge.read_bytes()
    manifest = json.loads(args.challenge.with_name("manifest.json").read_text())
    if hashlib.sha256(raw).hexdigest() != manifest["casesSha256"]:
        raise ValueError("challenge changed")
    settings = Settings()
    contract, questions = profile_contract("memory", MEMORY_EVIDENCE_CONTRACT)
    client = LocalDecisions(
        settings.memory_base_url, settings.memory_api_key, 15, contract, questions, 0.85
    )
    try:
        response = await client.client.get("/health")
        response.raise_for_status()
        before = response.json()
        if not before["ready"] or before["model"] != "psych-memory-domain-v8":
            raise ValueError("unexpected formal Memory model")
        cases, summary = [], defaultdict(lambda: defaultdict(int))
        for row in [json.loads(l) for l in raw.decode().splitlines()]:
            task, label = next(iter(row["expected"].items()))
            result = await client.evaluate(normalize(task, row["state"]), [task])
            answer = result["decisions"][task]
            cases.append(
                {
                    **row,
                    "task": task,
                    "label": label,
                    "choice": answer["choice"],
                    "decision": answer,
                }
            )
            item = summary[task]
            item["rows"] += 1
            item["rawCorrect"] += answer["choice"] == label
            item["falsePositive"] += answer["choice"] == "yes" and label == "no"
            item["falseNegative"] += answer["choice"] == "no" and label == "yes"
            item["usableCorrect"] += answer["accepted"] and answer["choice"] == label
            item["acceptedWrong"] += answer["accepted"] and answer["choice"] != label
            item["abstained"] += not answer["accepted"]
            if len(cases) % 60 == 0:
                print(json.dumps({"referenceCompleted": len(cases)}), flush=True)
        response = await client.client.get("/health")
        response.raise_for_status()
        if response.json() != before:
            raise ValueError("formal Memory changed")
        args.out.write_text(
            json.dumps(
                {
                    "model": before["model"],
                    "health": before,
                    "casesSha256": manifest["casesSha256"],
                    "cases": cases,
                    "summary": dict(summary),
                },
                ensure_ascii=False,
                indent=2,
            )
            + "\n"
        )
        print(json.dumps({"status": "complete", "summary": dict(summary)}), flush=True)
    finally:
        await client.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("env", "challenge", "out"):
        parser.add_argument("--" + name, type=Path, required=True)
    asyncio.run(run(parser.parse_args()))
