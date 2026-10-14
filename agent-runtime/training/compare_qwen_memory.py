"""Compare fixed raw-label probes and paired ranking, without fitting thresholds."""

import argparse
import json
from pathlib import Path

from psych_agent_runtime.bm25 import BM25

from .qwen_memory_probe import ranking_metrics, task_of


def compare(baseline, candidates):
    digest = baseline["casesSha256"]
    original = {c["name"]: c for c in baseline["cases"]}
    pairs = {}
    for case in original.values():
        if case["group"] == "fresh-pairs":
            pairs.setdefault(case["family"], []).append(case)
    correct = ties = 0
    for pair in pairs.values():
        scores = {
            h.index: h.score
            for h in BM25([c["state"]["candidate"]["content"] for c in pair]).search(
                pair[0]["state"]["query"], 2
            )
        }
        positive = next(i for i, c in enumerate(pair) if c["expected"]["relevance"] == "yes")
        a, b = scores.get(positive, 0), scores.get(1 - positive, 0)
        correct += a > b
        ties += a == b
    report = {
        "casesSha256": digest,
        "scope": "Raw-label and matched-pair comparison on authored development cases. No temperature/threshold fitting, no deployment or end-answer validation. Pair ranking is not corpus retrieval Recall@k.",
        "baseline": {
            "model": baseline["model"],
            "summary": baseline["summary"],
            "ranking": ranking_metrics(baseline["cases"]),
        },
        "BM25PairRanking": {"pairs": len(pairs), "positiveRankedFirst": correct, "ties": ties},
        "candidates": {},
    }
    for candidate in candidates:
        if candidate["casesSha256"] != digest or candidate["deploymentEnabled"]:
            raise ValueError("candidate is not the frozen offline experiment")
        modes = {}
        for mode, data in candidate["modes"].items():
            improvements, regressions, shared_wrong = [], [], []
            for case in data["cases"]:
                old = original[case["name"]]
                task = task_of(case)
                expected = case["expected"][task]
                if old["state"] != case["state"] or old["expected"] != case["expected"]:
                    raise ValueError("comparison inputs differ")
                was = old["prediction"]["choice"] == expected
                now = case["prediction"]["choice"] == expected
                if now and not was:
                    improvements.append(case["name"])
                elif was and not now:
                    regressions.append(case["name"])
                elif not was and not now:
                    shared_wrong.append(case["name"])
            modes[mode] = {
                "summary": data["summary"],
                "ranking": data["ranking"],
                "improvements": improvements,
                "regressions": regressions,
                "sharedWrong": shared_wrong,
                "batchMedianMs": data["batchMedianMs"],
                "timingScope": data["timingScope"],
            }
        report["candidates"][candidate["model"]] = {
            "revision": candidate["revision"],
            "parameterCount": candidate["parameterCount"],
            "calibrated": candidate["calibrated"],
            "peakAllocatedMB": candidate["peakAllocatedMB"],
            "modes": modes,
        }
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline", type=Path, required=True)
    parser.add_argument("--candidate", type=Path, action="append", required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    if args.out.exists():
        raise ValueError("refuse to overwrite comparison")
    report = compare(
        json.loads(args.baseline.read_text()), [json.loads(p.read_text()) for p in args.candidate]
    )
    args.out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
