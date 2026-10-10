"""Evaluate a frozen authored challenge via the actual authenticated worker API."""

import argparse
import hashlib
import json
import os
import time
from pathlib import Path

import httpx

from psych_agent_runtime.decision_contract import PROFILES


def evaluate(data, out, suffix="", profile=None):
    raw = data.read_bytes()
    cases = [json.loads(line) for line in raw.decode().splitlines() if line]
    results = []
    for case in cases:
        role = case["profile"]
        if profile and role != profile:
            continue
        env = role.upper()
        url = os.environ.get(env + "_CHALLENGE_URL") or os.environ[env + "_BASE_URL"]
        with httpx.Client(
            base_url=url,
            headers={"Authorization": "Bearer " + os.environ[env + "_API_KEY"]},
            timeout=60,
            trust_env=False,
        ) as client:
            start = time.monotonic()
            r = client.post(
                "/v1/decisions",
                json={
                    "contractVersion": PROFILES[role][0],
                    "state": case["state"],
                    "tasks": list(case["expected"]),
                },
            )
            r.raise_for_status()
            payload = r.json()
            answers = payload["answers"]
        got = {task: answer["choice"] for task, answer in answers.items()}
        minimum = float(os.environ.get(env + "_MIN_PROBABILITY", "0.85"))
        accepted = all(
            answer["accepted"]
            and answer.get("calibrated") is True
            and answer["probabilities"][answer["choice"]] >= max(minimum, answer["threshold"])
            for answer in answers.values()
        )
        item = {
            **case,
            "actual": got,
            "correct": got == case["expected"],
            "accepted": accepted,
            "usableCorrect": accepted and got == case["expected"],
            "model": payload["model"],
            "latencyMs": round((time.monotonic() - start) * 1000, 2),
            "confidence": {
                t: {"probability": a["probabilities"][a["choice"]], "threshold": a["threshold"]}
                for t, a in answers.items()
            },
        }
        item["clientMinimumProbability"] = minimum
        results.append(item)
        print(
            json.dumps(
                {
                    "name": case["name"],
                    "profile": role,
                    "correct": item["correct"],
                    "accepted": accepted,
                    "latencyMs": item["latencyMs"],
                }
            ),
            flush=True,
        )
    summary = {}
    for role in {c["profile"] for c in results}:
        rows = [r for r in results if r["profile"] == role]
        lat = sorted(r["latencyMs"] for r in rows)
        summary[role] = {
            "rows": len(rows),
            "correct": sum(r["correct"] for r in rows),
            "usableCorrect": sum(r["usableCorrect"] for r in rows),
            "acceptedWrong": sum(r["accepted"] and not r["correct"] for r in rows),
            "p50LatencyMs": lat[len(lat) // 2],
            "p95LatencyMs": lat[min(len(lat) - 1, int(len(lat) * 0.95))],
        }
    report = {
        "challengeSha256": hashlib.sha256(raw).hexdigest(),
        "evaluation": suffix,
        "scope": "Frozen authored boundary diagnostics, not real-dialogue or clinical validation. Never used for checkpoint selection.",
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
    p.add_argument("--label", default="")
    p.add_argument("--profile", choices=["executor", "memory"])
    a = p.parse_args()
    evaluate(a.data, a.out, a.label, a.profile)
