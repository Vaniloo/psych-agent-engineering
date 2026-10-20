"""Replay frozen binary goal decisions using the same serving acceptance policy."""

import argparse
import hashlib
import json
from pathlib import Path

from psych_agent_runtime.goal_gate_contract import CONTRACT
from psych_agent_runtime.goal_model_gate import positive, unambiguous_positive
from psych_agent_runtime.qwen_memory_service import calibrated_answer


def replay(rows, policies):
    answers = {}
    for row in rows:
        task = row["task"]
        value = calibrated_answer(policies[task], 0, row["logitDifference"])
        value["accepted"] = value["accepted"] and max(value["probabilities"].values()) >= 0.90
        key = (row["state"].get("candidateIndex") if task == "candidate_match" else None)
        answers[(task, key)] = value
    decisions = [answers[(t, None)] for t in ("goal_related", "create_request", "progress_report")]
    if not all(d["accepted"] for d in decisions):
        return {"operation": "abstain", "reason": "gate_uncertain"}
    related, create, progress = [positive(d) for d in decisions]
    if not related:
        return {"operation": "none"}
    if create and progress:
        return {"operation": "clarify", "reason": "conflicting_intents"}
    if create:
        return {"operation": "create"}
    if not progress:
        return {"operation": "read"}
    candidates = rows[0]["state"]["candidates"]
    if not 1 <= len(candidates) <= 8:
        return {"operation": "clarify", "reason": "candidate_budget"}
    selected = unambiguous_positive([answers[("candidate_match", i)] for i in range(len(candidates))])
    if selected is None or candidates[selected]["status"] != "planned":
        return {"operation": "clarify", "reason": "unresolved_step"}
    tasks = ["outcome_done", "outcome_partial", "outcome_blocked", "outcome_rest"]
    outcome = unambiguous_positive([answers[(t, None)] for t in tasks])
    if outcome is None:
        return {"operation": "clarify", "reason": "outcome_uncertain"}
    return {"operation": "progress", "target": selected, "outcome": tasks[outcome].removeprefix("outcome_")}


def evaluate(checkpoint, challenge):
    report = json.loads((checkpoint / "training-report.json").read_text())
    if report["status"] != "complete" or report["contractVersion"] != CONTRACT:
        raise ValueError("completed goal gate report required")
    raw = (challenge / "cases.jsonl").read_bytes()
    if hashlib.sha256(raw).hexdigest() != report["challengeSha256"]:
        raise ValueError("frozen challenge changed")
    scenarios = json.loads((challenge / "scenarios.json").read_text())
    rows = json.loads((checkpoint / "challenge-results.json").read_text())["cases"]
    results = []
    for scenario in scenarios:
        subset = [r for r in rows if r["family"] == scenario["name"]]
        if not subset:
            raise ValueError("missing challenge decisions")
        actual = replay(subset, report["taskPolicies"])
        labels = scenario["expectedPositive"]
        target = scenario["target"]
        expected = {"operation": "none"}
        if "create_request" in labels:
            expected = {"operation": "create"}
        elif "progress_report" in labels and target is not None:
            outcome = next(t.removeprefix("outcome_") for t in labels if t.startswith("outcome_"))
            expected = {"operation": "progress", "target": target, "outcome": outcome}
        wants_write = expected["operation"] in {"create", "progress"}
        writes = actual["operation"] in {"create", "progress"}
        correct_write = wants_write and actual == expected
        results.append({"name": scenario["name"], "suite": scenario["suite"], "expected": expected,
                        "actual": actual, "correctWrite": correct_write,
                        "missedWrite": wants_write and not correct_write,
                        "wrongWrite": writes and not correct_write})
    return {"model": report["modelName"], "adapterSha256": report["adapterSha256"],
            "scope": "29 authored scenarios; not independent human evaluation or a production error guarantee",
            "summary": {"scenarios": len(results),
                        **{key: sum(r[key] for r in results) for key in ("correctWrite", "missedWrite", "wrongWrite")}},
            "results": results}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--checkpoint", type=Path, required=True)
    parser.add_argument("--challenge", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    result = evaluate(args.checkpoint, args.challenge)
    args.out.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps(result["summary"]))
