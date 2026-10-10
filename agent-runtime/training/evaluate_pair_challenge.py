"""Compare versioned memory representations on the same frozen evidence cases.

Requests use actual authenticated workers. Pair results are mapped back to the
original candidate indices; evidence and expected labels are never rewritten.
"""

import argparse
import hashlib
import json
import os
import time
from pathlib import Path

import httpx

from psych_agent_runtime.decision_contract import MEMORY_PAIR_CONTRACT, profile_contract


def evaluate(data, out, profile, url, key, label):
    raw = data.read_bytes()
    cases = [json.loads(line) for line in raw.decode().splitlines() if line]
    results = []
    with httpx.Client(
        base_url=url, headers={"Authorization": "Bearer " + key}, timeout=60, trust_env=False
    ) as client:
        health = client.get("/health").json()
        contract, _ = profile_contract(profile, health["contractVersion"])
        for case in cases:
            if case["profile"] != profile:
                continue
            started = time.monotonic()
            expected, state = case["expected"], case["state"]
            answers = {}
            if contract == MEMORY_PAIR_CONTRACT:
                for task in expected:
                    if not task.startswith("relevance_"):
                        continue
                    candidate = dict(state["candidates"][int(task.split("_")[1])])
                    if "role" not in candidate:
                        candidate["role"] = (
                            "assistant" if candidate["content"].startswith("助手建议：") else "user"
                        )
                    response = client.post(
                        "/v1/decisions",
                        json={
                            "contractVersion": contract,
                            "state": {"query": state["query"], "candidate": candidate},
                            "tasks": ["relevance"],
                        },
                    )
                    response.raise_for_status()
                    answers[task] = response.json()["answers"]["relevance"]
            remaining = [t for t in expected if t not in answers]
            if remaining:
                response = client.post(
                    "/v1/decisions",
                    json={
                        "contractVersion": contract,
                        "state": state,
                        "tasks": remaining,
                    },
                )
                response.raise_for_status()
                answers.update(response.json()["answers"])
            actual = {t: a["choice"] for t, a in answers.items()}
            accepted = all(
                a["accepted"]
                and a["calibrated"] is True
                and a["probabilities"][a["choice"]] >= max(0.85, a["threshold"])
                for a in answers.values()
            )
            results.append(
                {
                    **case,
                    "actual": actual,
                    "correct": actual == expected,
                    "accepted": accepted,
                    "usableCorrect": accepted and actual == expected,
                    "model": health["model"],
                    "contractVersion": contract,
                    "latencyMs": round((time.monotonic() - started) * 1000, 2),
                    "confidence": {
                        t: {
                            "probability": a["probabilities"][a["choice"]],
                            "threshold": a["threshold"],
                        }
                        for t, a in answers.items()
                    },
                    "clientMinimumProbability": 0.85,
                }
            )
    summary = {
        profile: {
            "rows": len(results),
            "correct": sum(r["correct"] for r in results),
            "usableCorrect": sum(r["usableCorrect"] for r in results),
            "acceptedWrong": sum(r["accepted"] and not r["correct"] for r in results),
        }
    }
    report = {
        "challengeSha256": hashlib.sha256(raw).hexdigest(),
        "evaluation": label,
        "scope": "Same frozen cases with versioned candidate-pair input adapter; development diagnostics, not original LongMemEval QA or clinical accuracy.",
        "summary": summary,
        "cases": results,
    }
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2))
    print(json.dumps(summary), flush=True)
    return report


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--data", type=Path, required=True)
    p.add_argument("--out", type=Path, required=True)
    p.add_argument("--profile", choices=["memory", "router"], required=True)
    p.add_argument("--url", required=True)
    p.add_argument("--label", default="pair-round7")
    a = p.parse_args()
    evaluate(a.data, a.out, a.profile, a.url, os.environ[a.profile.upper() + "_API_KEY"], a.label)
