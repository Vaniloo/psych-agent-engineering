"""Fixed-weight offline calibration proposals; no checkpoint or worker writes."""

import argparse
import copy
import gc
import hashlib
import json
import os
from pathlib import Path

from psych_agent_runtime.decision_contract import profile_contract
from psych_agent_runtime.routing_contract import validate_probabilities
from psych_agent_runtime.routing_service import calibrate_probabilities

from .audit_calibration_data import digest, without_record_stamp
from .decision_data import selected_questions
from .evaluate_readiness import matches, summary
from .offline_task_evaluation import load_agent
from .stratified_calibration import acceptance, fit

TARGETS = {"router": ["risk_band"], "executor": ["goal_action", "goal_outcome"]}


def infer_rows(agent, adapter, metadata, rows, unrounded=False):
    result = []
    profile = metadata["profile"]
    _, canonical = profile_contract(profile, metadata["contractVersion"])
    for row in rows:
        tasks = list(canonical) if profile == "router" else list(row["expected"])
        if profile == "executor" and "goal_action" in tasks and "goal_outcome" not in tasks:
            tasks.append("goal_outcome")
        questions = selected_questions(profile, row["state"], tasks, metadata["contractVersion"])
        state_text = json.dumps(row["state"], ensure_ascii=False).replace(agent.tok.mask_token, " ")
        if len(agent.tok.encode(state_text, add_special_tokens=False)) > metadata["maxStateTokens"]:
            raise ValueError("calibration state exceeds serving token budget")
        output = (adapter.predict if adapter else agent.predict)(row["state"], questions)
        answers = {
            t: {
                "choice": a["choice"],
                "probabilities": validate_probabilities(
                    a["choice"],
                    a["unroundedProbabilities"] if unrounded else a["probabilities"],
                    questions[t]["criteria"],
                ),
                **({"roundedProbabilities": a["probabilities"]} if unrounded else {}),
            }
            for t, a in output["answers"].items()
        }
        result.append({**row, "rawAnswers": answers})
    return result


def records(rows, task):
    return [
        {
            "group": row["calibrationGroup"],
            "expected": row["expected"][task],
            **row["rawAnswers"][task],
        }
        for row in rows
        if task in row["expected"]
    ]


def replay(rows, metadata, proposals=None):
    cases = []
    for row in rows:
        item = copy.deepcopy(row)
        decisions = {}
        for task, answer in row["rawAnswers"].items():
            policy = (proposals or {}).get(task, metadata["tasks"][task])
            probs = calibrate_probabilities(answer["probabilities"], policy)
            choice = answer["choice"]
            if "classPolicies" in policy:
                accepted = acceptance(task, choice, probs, policy)
                from .stratified_calibration import bucket

                threshold = max(0.85, policy["classPolicies"][bucket(task, choice)]["threshold"])
            else:
                threshold = max(0.85, policy["threshold"])
                accepted = policy["enabled"] and policy["calibrated"] and probs[choice] >= threshold
            decisions[task] = {
                "choice": choice,
                "probabilities": probs,
                "probability": probs[choice],
                "threshold": threshold,
                "accepted": bool(accepted),
                "status": "accepted" if accepted else "abstained",
                "scope": policy["validationScope"],
            }
        item.update(
            decisions=decisions,
            correct=all(
                matches(decisions[t]["choice"], label) for t, label in row["expected"].items()
            ),
            accepted=all(decisions[t]["accepted"] for t in row["expected"]),
        )
        item["usableCorrect"] = item["correct"] and item["accepted"]
        item.setdefault(
            "family", row.get("calibrationGroup", row.get("splitGroup", row.get("name", "")))
        )
        item.setdefault("critical", "")
        item.setdefault("latencyMs", 0)
        item.setdefault("applicationExpected", {})
        item.setdefault("name", row.get("splitGroup", item["family"]))
        item["application"] = {}
        item.pop("rawAnswers", None)
        cases.append(item)
    return cases


def split_summary(cases):
    result = summary(cases)
    result.pop("p50LatencyMs")
    result.pop("p95LatencyMs")
    return result


def run(args):
    import torch

    if args.out.exists():
        raise ValueError("refuse to overwrite experiment")
    args.out.mkdir()
    os.environ["LAYA_CUDA_AMP"] = "fp16"
    torch.set_num_threads(4)
    torch.cuda.set_per_process_memory_fraction(0.20)
    cohort_manifest = json.loads((args.cohort / "cohort-manifest.json").read_text())
    fresh_raw = args.fresh.read_bytes()
    fresh_hash = hashlib.sha256(fresh_raw).hexdigest()
    if (
        fresh_hash
        != json.loads(args.fresh.with_name("manifest.json").read_text())["challengeSha256"]
    ):
        raise ValueError("fresh challenge hash mismatch")
    fresh = [json.loads(line) for line in fresh_raw.decode().splitlines() if line]
    legacy_raw = args.legacy.read_bytes()
    legacy = [json.loads(line) for line in legacy_raw.decode().splitlines() if line]
    models = {
        "router": ["psych-router-domain-v5", "psych-router-isolated-r11"],
        "executor": ["psych-executor-domain-v6", "psych-executor-isolated-r11"],
    }
    final = {
        "cohortManifest": cohort_manifest,
        "freshChallengeSha256": fresh_hash,
        "legacyChallengeSha256": hashlib.sha256(legacy_raw).hexdigest(),
        "models": {},
        "weightTrainingPerformed": False,
        "promotedModels": [],
        "independentReviewVerified": False,
        "unroundedDecoding": args.unrounded,
    }
    for role, checkpoints in models.items():
        splits = {}
        known_inputs = set()
        for split in ("calibration", "validation", "test"):
            path = args.cohort / role / (split + ".jsonl")
            if (
                hashlib.sha256(path.read_bytes()).hexdigest()
                != cohort_manifest[role]["datasetSha256"][split]
            ):
                raise ValueError("cohort changed after freezing")
            splits[split] = [json.loads(line) for line in path.read_text().splitlines() if line]
            known_inputs.update(digest(without_record_stamp(row)) for row in splits[split])
        heldouts = {
            "fresh": [r for r in fresh if r["profile"] == role],
            "legacy": [r for r in legacy if r["profile"] == role],
        }
        if any(digest(without_record_stamp(row)) in known_inputs for row in heldouts["fresh"]):
            raise ValueError("fresh holdout overlaps calibration cohort")
        for model_name in checkpoints:
            checkpoint = args.models / model_name
            before = hashlib.sha256((checkpoint / "deployment.json").read_bytes()).hexdigest()
            agent, adapter, metadata = load_agent(checkpoint, "cuda:0")
            if args.unrounded:
                from .unrounded_laya import enable_unrounded_decoding

                enable_unrounded_decoding(agent)
            output = {
                "model": model_name,
                "metadataBeforeSha256": before,
                "metadata": metadata,
                "raw": {},
                "proposals": {},
            }
            for split in ("calibration", "validation"):
                output["raw"][split] = infer_rows(
                    agent, adapter, metadata, splits[split], args.unrounded
                )
                print(
                    json.dumps(
                        {"model": model_name, "stage": split, "records": len(splits[split])}
                    ),
                    flush=True,
                )
            # No heldout inference is performed before proposals are fitted.
            for task in TARGETS[role]:
                output["proposals"][task] = fit(
                    task,
                    records(output["raw"]["calibration"], task),
                    records(output["raw"]["validation"], task),
                )
            output["results"] = {}
            for split, rows in {"test": splits["test"], **heldouts}.items():
                raw = infer_rows(agent, adapter, metadata, rows, args.unrounded)
                output["raw"][split] = raw
                old, new = replay(raw, metadata), replay(raw, metadata, output["proposals"])
                output["results"][split] = {
                    "originalPolicy": split_summary(old),
                    "proposedPolicy": split_summary(new),
                    "originalCases": old,
                    "proposedCases": new,
                }
                if args.unrounded:
                    rounded = copy.deepcopy(raw)
                    for row in rounded:
                        for answer in row["rawAnswers"].values():
                            answer["probabilities"] = answer["roundedProbabilities"]
                    actual = replay(rounded, metadata)
                    output["results"][split]["roundedOriginalPolicy"] = split_summary(actual)
                    output["results"][split]["roundedOriginalCases"] = actual
                print(
                    json.dumps(
                        {
                            "model": model_name,
                            "stage": split,
                            "old": split_summary(old),
                            "new": split_summary(new),
                        }
                    ),
                    flush=True,
                )
            # Check single-task versus actual serving batch; recorded as an audit,
            # never used to choose temperatures or override frozen labels.
            if role == "router":
                differences = []
                for row in output["raw"]["calibration"][:20]:
                    q = selected_questions(
                        role, row["state"], ["risk_band"], metadata["contractVersion"]
                    )
                    raw = (adapter.predict if adapter else agent.predict)(row["state"], q)[
                        "answers"
                    ]["risk_band"]
                    full = row["rawAnswers"]["risk_band"]
                    differences.append(
                        {
                            "choiceChanged": raw["choice"] != full["choice"],
                            "maximumProbabilityDelta": max(
                                abs(
                                    (
                                        raw["unroundedProbabilities"]
                                        if args.unrounded
                                        else raw["probabilities"]
                                    )[k]
                                    - p
                                )
                                for k, p in full["probabilities"].items()
                            ),
                        }
                    )
                output["batchContextAudit"] = {
                    "rows": len(differences),
                    "choicesChanged": sum(d["choiceChanged"] for d in differences),
                    "maximumProbabilityDelta": max(
                        d["maximumProbabilityDelta"] for d in differences
                    ),
                }
            if hashlib.sha256((checkpoint / "deployment.json").read_bytes()).hexdigest() != before:
                raise ValueError("model metadata changed during read-only experiment")
            (args.out / (model_name + ".json")).write_text(
                json.dumps(output, ensure_ascii=False, indent=2)
            )
            final["models"][model_name] = {
                "profile": role,
                "proposals": output["proposals"],
                "results": {
                    s: {k: v for k, v in r.items() if not k.endswith("Cases")}
                    for s, r in output["results"].items()
                },
                "batchContextAudit": output.get("batchContextAudit"),
            }
            del output, agent, adapter
            gc.collect()
            torch.cuda.empty_cache()
    (args.out / "summary.json").write_text(json.dumps(final, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("cohort", "models", "fresh", "legacy", "out"):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument(
        "--unrounded", action="store_true", help="Offline full-precision probability decoding"
    )
    run(parser.parse_args())
