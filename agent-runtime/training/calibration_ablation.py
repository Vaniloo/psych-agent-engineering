"""Cross archived weights and policies without fitting on diagnostic labels.

Temperature inversion reconstructs pre-service probabilities, not model logits.
The service's 1e-30 clamp is irreversible: verify the original-policy roundtrip,
report the caveat, and do not use reconstructed tails as calibration training.
"""

import argparse
import copy
import json
import math
from collections import Counter
from pathlib import Path

from .evaluate_readiness import matches
from .train_decisions import scaled


def invert(probs, policy):
    if not policy.get("calibrated"):
        return probs.copy()
    temperature = policy["temperature"]
    if not math.isfinite(temperature) or temperature <= 0:
        raise ValueError("invalid calibration temperature")
    if any(not math.isfinite(p) or p < 0 for p in probs.values()) or not math.isclose(
        sum(probs.values()), 1, abs_tol=0.002
    ):
        raise ValueError("invalid recorded probabilities")
    logits = {key: math.log(max(p, 1e-300)) * temperature for key, p in probs.items()}
    peak = max(logits.values())
    scores = {key: math.exp(value - peak) for key, value in logits.items()}
    total = sum(scores.values())
    return {key: value / total for key, value in scores.items()}


def replay(rows, origin, target, minimum=0.85):
    output = []
    max_error = 0
    for row in rows:
        if row.get("errorType"):
            raise ValueError("cannot ablate API failures")
        item = copy.deepcopy(row)
        for task, decision in item["decisions"].items():
            raw = invert(decision["probabilities"], origin[task])
            roundtrip = scaled(raw, origin[task]["temperature"])
            max_error = max(
                max_error,
                max(abs(roundtrip[k] - decision["probabilities"][k]) for k in raw),
            )
            if max_error > 1e-9:
                raise ValueError("recorded policy probability roundtrip failed")
            policy = target[task]
            probs = scaled(raw, policy["temperature"]) if policy.get("calibrated") else raw
            choice = decision["choice"]
            accepted = (
                policy.get("enabled") is True
                and policy.get("calibrated") is True
                and probs[choice] >= max(minimum, policy["threshold"])
            )
            decision.update(
                probabilities=probs,
                probability=probs[choice],
                threshold=policy["threshold"],
                accepted=accepted,
                status="accepted" if accepted else "abstained",
            )
        item["correct"] = all(
            matches(item["decisions"][task]["choice"], label)
            for task, label in item["expected"].items()
        )
        item["accepted"] = all(item["decisions"][t]["accepted"] for t in item["expected"])
        item["usableCorrect"] = item["accepted"] and item["correct"]
        # Old application results no longer correspond to the crossed policy.
        item.pop("application", None)
        output.append(item)
    return output, max_error


def metrics(rows):
    tasks = {}
    for task in sorted({t for r in rows for t in r["expected"]}):
        examples = [(r, r["decisions"][task]) for r in rows if task in r["expected"]]
        tasks[task] = {
            "items": len(examples),
            "rawCorrect": sum(matches(d["choice"], r["expected"][task]) for r, d in examples),
            "usableCorrect": sum(
                d["accepted"] and matches(d["choice"], r["expected"][task]) for r, d in examples
            ),
            "acceptedWrong": sum(
                d["accepted"] and not matches(d["choice"], r["expected"][task]) for r, d in examples
            ),
            "abstained": sum(not d["accepted"] for _, d in examples),
            "criticalAcceptedErrors": [
                r["name"]
                for r, d in examples
                if r["critical"] and d["accepted"] and not matches(d["choice"], r["expected"][task])
            ],
            "wrongLabels": dict(
                Counter(
                    str(r["expected"][task]) + " -> " + d["choice"]
                    for r, d in examples
                    if not matches(d["choice"], r["expected"][task])
                )
            ),
        }
    return {
        "items": len(rows),
        "rawCorrect": sum(r["correct"] for r in rows),
        "usableCorrect": sum(r["usableCorrect"] for r in rows),
        "acceptedWrong": sum(r["accepted"] and not r["correct"] for r in rows),
        "abstained": sum(not r["accepted"] for r in rows),
        "tasks": tasks,
    }


def ablate(baseline, candidate, policies):
    if baseline["challengeSha256"] != candidate["challengeSha256"]:
        raise ValueError("challenge differs")
    result = {"challengeSha256": baseline["challengeSha256"], "roles": {}}
    for role, role_policies in policies.items():
        sources = {"baseline": baseline["roles"][role], "candidate": candidate["roles"][role]}
        left = sources["baseline"]["cases"]
        right = sources["candidate"]["cases"]
        if len(left) != len(right) or any(
            any(a.get(k) != b.get(k) for k in ("name", "state", "expected", "critical"))
            for a, b in zip(left, right, strict=True)
        ):
            raise ValueError("frozen cases differ or reordered")
        variants = {}
        for weights, source in sources.items():
            if source["health"]["model"] != role_policies[weights]["model"]:
                raise ValueError("model identity differs")
            if (
                source["health"]["artifact"]["weightsSha256"]
                != role_policies[weights]["weightsSha256"]
            ):
                raise ValueError("weights metadata differs")
            if source["health"]["artifact"].get("isolatedTaskAdapters") != role_policies[
                weights
            ].get("isolatedTaskAdapters"):
                raise ValueError("isolated task adapter metadata differs")
            for policy, metadata in role_policies.items():
                rows, error = replay(
                    source["cases"], role_policies[weights]["tasks"], metadata["tasks"]
                )
                if weights == policy and any(
                    row["decisions"][t]["accepted"] != old["decisions"][t]["accepted"]
                    for row, old in zip(rows, source["cases"], strict=True)
                    for t in row["decisions"]
                ):
                    raise ValueError("archived acceptance not reproduced")
                variants[f"{weights}_weights__{policy}_policy"] = {
                    "weightsModel": source["health"]["model"],
                    "policyModel": metadata["model"],
                    "maximumRoundtripError": error,
                    **metrics(rows),
                }
        result["roles"][role] = variants
    result.update(
        method="Archived service distributions, inverse temperature, crossed policy replay.",
        minimumProbability=0.85,
        policyFittedOnTheseCases=False,
        independentReviewVerified=False,
        limitations=[
            "1e-30 probability clamp tails cannot be recovered; roundtrip is checked to 1e-9.",
            "Same-policy acceptance is reproduced exactly; crossed policy is a numerical reconstruction.",
            "Diagnostic cases are development data, not independent human/clinical gold.",
            "No application probe or latency benchmark is inferred from crossed policies.",
        ],
    )
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    for key in ("baseline", "candidate", "metadata", "out"):
        parser.add_argument("--" + key, type=Path, required=True)
    args = parser.parse_args()
    output = ablate(
        json.loads(args.baseline.read_text()),
        json.loads(args.candidate.read_text()),
        json.loads(args.metadata.read_text()),
    )
    args.out.write_text(json.dumps(output, ensure_ascii=False, indent=2) + "\n")
    print(
        json.dumps(
            {r: {v: x["usableCorrect"] for v, x in d.items()} for r, d in output["roles"].items()}
        )
    )
