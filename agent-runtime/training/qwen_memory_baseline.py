"""Read-only same-case baseline from the existing Memory worker, with provenance."""

import argparse
import asyncio
import hashlib
import json
import os
import shlex
import time
from pathlib import Path

from .qwen_memory_probe import summarize, task_of


async def run(args):
    # Only the decision connection is read; never execute/source a private env
    # file or copy its contents into an experiment output.
    for line in args.env.read_text().splitlines():
        key, sep, value = line.removeprefix("export ").partition("=")
        if sep and key in {"MEMORY_BASE_URL", "MEMORY_API_KEY"}:
            parts = shlex.split(value)
            os.environ[key] = parts[0] if parts else ""
    from psych_agent_runtime.config import Settings
    from psych_agent_runtime.decision_contract import (
        MEMORY_EVIDENCE_CONTRACT,
        memory_evidence_state,
        memory_pair_state,
        profile_contract,
    )
    from psych_agent_runtime.decision_router import LocalDecisions

    if args.out.exists():
        raise ValueError("refuse to overwrite baseline")
    raw = args.data.read_bytes()
    manifest = json.loads(args.data.with_name("manifest.json").read_text())
    if hashlib.sha256(raw).hexdigest() != manifest["casesSha256"]:
        raise ValueError("frozen cases changed")
    settings = Settings()
    contract, questions = profile_contract("memory", MEMORY_EVIDENCE_CONTRACT)
    client = LocalDecisions(
        settings.memory_base_url,
        settings.memory_api_key,
        10,
        contract,
        questions,
        0.85,
    )
    try:
        response = await client.client.get("/health")
        response.raise_for_status()
        before = response.json()
        if not before.get("ready") or before.get("model") != "psych-memory-domain-v8":
            raise ValueError("unexpected formal baseline model")
        results = []
        for row in [json.loads(line) for line in raw.decode().splitlines() if line]:
            task = task_of(row)
            normalize = memory_pair_state if task == "relevance" else memory_evidence_state
            start = time.monotonic()
            answer = (await client.evaluate(normalize(row["state"]), [task]))["decisions"][task]
            results.append(
                {
                    **row,
                    "prediction": {
                        "choice": answer["choice"],
                        "yesScore": answer["probabilities"]["yes"],
                        "accepted": answer["accepted"],
                        "calibrated": True,
                    },
                    "decision": answer,
                    "latencyMs": (time.monotonic() - start) * 1000,
                }
            )
            if len(results) % 30 == 0:
                print(json.dumps({"baselineCompleted": len(results)}), flush=True)
        response = await client.client.get("/health")
        response.raise_for_status()
        after = response.json()
        if before != after:
            raise ValueError("formal baseline changed during experiment")
        report = {
            "model": before["model"],
            "healthBefore": before,
            "healthAfter": after,
            "casesSha256": manifest["casesSha256"],
            "summary": summarize(results),
            "cases": results,
            "deploymentChanged": False,
            "scope": "Same synthetic cases, serial existing-worker HTTP; not production load or clinical validation. Compare raw labels separately from calibrated acceptance.",
        }
        args.out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
        print(json.dumps({"status": "complete", "summary": report["summary"]}), flush=True)
    finally:
        await client.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("data", "env", "out"):
        parser.add_argument("--" + name, type=Path, required=True)
    asyncio.run(run(parser.parse_args()))
