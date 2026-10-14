"""Bundle targeted parameters with unchanged base and unrelated policies."""

import argparse
import json
import shutil
from pathlib import Path

from psych_agent_runtime.decision_contract import profile_contract

from .bundle_memory_head import checksum


def bundle(previous, trained, out, tasks):
    from safetensors import safe_open

    if out.exists():
        raise ValueError("refuse to overwrite task bundle")
    old = json.loads((previous / "deployment.json").read_text())
    new = json.loads((trained / "deployment.json").read_text())
    _, questions = profile_contract(old["profile"], old["contractVersion"])
    if (
        not tasks
        or len(set(tasks)) != len(tasks)
        or any(t not in questions for t in tasks)
        or old.get("promotionStatus") != "approved"
        or new["profile"] != old["profile"]
        or new["contractVersion"] != old["contractVersion"]
        or new["questionsSha256"] != old["questionsSha256"]
        or old.get("isolatedTaskAdapters")
        or new.get("calibrationProbabilityEncoding") not in {None, "laya_probability_rounded4_v1"}
        or (old.get("pairAdapterSha256") and set(tasks) & {"relevance", "evidence_sufficient"})
    ):
        raise ValueError("task bundle contract or provenance mismatch")
    for checkpoint, metadata in [(previous, old), (trained, new)]:
        if checksum(checkpoint / "model.safetensors") != metadata["weightsSha256"]:
            raise ValueError("artifact checksum mismatch")
    old_cfg = json.loads((previous / "rl_agent_config.json").read_text())
    new_cfg = json.loads((trained / "rl_agent_config.json").read_text())
    for key in ("encoder", "head_layers", "max_len", "head_max_len", "temperature"):
        if old_cfg[key] != new_cfg[key]:
            raise ValueError("inference config differs")
    with (
        safe_open(str(previous / "model.safetensors"), framework="pt", device="cpu") as base,
        safe_open(str(trained / "model.safetensors"), framework="pt", device="cpu") as adapted,
    ):
        keys = base.keys()
        if set(keys) != set(adapted.keys()) or any(
            base.get_slice(k).get_shape() != adapted.get_slice(k).get_shape() for k in keys
        ):
            raise ValueError("adapter architecture mismatch")
    report = json.loads((trained / "training-report.json").read_text())
    if set(report["trainingConfig"]["targetTasks"]) != set(tasks):
        raise ValueError("trained tasks differ from bundle ownership")
    shutil.copytree(previous, out)
    filename = "isolated-task-adapter.safetensors"
    shutil.copy2(trained / "model.safetensors", out / filename)
    metadata = {
        **old,
        "model": out.name,
        "previousBundle": previous.name,
        "isolatedBaseWeightsSha256": old["weightsSha256"],
        "isolatedTaskAdapters": [
            {
                "tasks": tasks,
                "file": filename,
                "sha256": new["weightsSha256"],
                "sourceModel": trained.name,
            }
        ],
        "tasks": {**old["tasks"], **{t: new["tasks"][t] for t in tasks}},
        "promotionStatus": "candidate",
        "promotionReasons": [],
        "promotionEvaluation": None,
        "labelProvenance": new.get("labelProvenance"),
        "trainingScope": "isolated_task_parameter_copy_with_unchanged_other_answers_and_policies",
        "targetTraining": {
            k: new.get(k) for k in ("trainedAt", "dataRows", "datasetSha256", "trainingSources")
        },
    }
    report.update(
        adapterTasks=tasks,
        unchangedBaseWeightsSha256=old["weightsSha256"],
        unchangedPolicies=[t for t in questions if t not in tasks],
        scope="Only target task metrics were measured here; frozen diagnostics and untouched-task parity are separate gates.",
    )
    (out / "deployment.json").write_text(json.dumps(metadata, ensure_ascii=False, indent=2))
    (out / "training-report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2))
    return metadata


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    for key in ("previous", "trained", "out"):
        parser.add_argument("--" + key, type=Path, required=True)
    parser.add_argument("--tasks", nargs="+", required=True)
    args = parser.parse_args()
    print(json.dumps(bundle(args.previous, args.trained, args.out, args.tasks), indent=2))
