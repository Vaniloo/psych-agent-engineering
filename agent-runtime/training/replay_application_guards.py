"""Replay native/archive decisions on runtime-host guards before release review."""

import argparse
import asyncio
import hashlib
import json
from pathlib import Path

from .evaluate_readiness import application_probe, summary


async def replay(source, out):
    if out.exists():
        raise ValueError("refuse to overwrite guard replay")
    report = json.loads(source.read_text())
    for role in report["roles"].values():
        for case in role["cases"]:
            case["application"] = await application_probe(case, case["decisions"])
        role["all"] = summary(role["cases"])
        role["groups"] = {
            group: summary([c for c in role["cases"] if c["evaluationGroup"] == group])
            for group in sorted({c["evaluationGroup"] for c in role["cases"]})
        }
    runtime = Path(__file__).resolve().parents[1] / "psych_agent_runtime"
    report.update(
        applicationProbesDeferred=False,
        sourceInferenceSha256=hashlib.sha256(source.read_bytes()).hexdigest(),
        applicationGuardSha256={
            name: hashlib.sha256((runtime / name).read_bytes()).hexdigest()
            for name in (
                "safety.py",
                "graph.py",
                "goal_evidence.py",
                "goal_progress.py",
                "goal_creation.py",
            )
        },
        applicationScope="Python runtime probes using recorded native decisions; no live Java persistence, reply generation, or business writes.",
    )
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2))
    return {role: value["all"] for role, value in report["roles"].items()}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("source", "out"):
        parser.add_argument("--" + name, type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(asyncio.run(replay(args.source, args.out)), indent=2))
