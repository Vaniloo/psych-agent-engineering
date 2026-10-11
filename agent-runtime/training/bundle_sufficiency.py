"""Attach an independent sufficiency model without changing existing Memory tasks."""

import argparse
import json
import shutil
from pathlib import Path

from psych_agent_runtime.decision_contract import (
    MEMORY_EVIDENCE_CONTRACT,
    MEMORY_EVIDENCE_QUESTIONS,
)
from psych_agent_runtime.routing_service import questions_digest
from training.bundle_memory_head import checksum


def bundle(previous, trained, out):
    from safetensors import safe_open

    if out.exists():
        raise ValueError("refuse to overwrite memory bundle")
    old = json.loads((previous / "deployment.json").read_text())
    new = json.loads((trained / "deployment.json").read_text())
    if (
        old.get("promotionStatus") != "approved"
        or old.get("profile") != "memory"
        or new["contractVersion"] != MEMORY_EVIDENCE_CONTRACT
        or not old.get("pairAdapterSha256")
    ):
        raise ValueError("memory bundle provenance mismatch")
    for path, want in [
        (previous / "model.safetensors", old["weightsSha256"]),
        (previous / "pair-adapter.safetensors", old["pairAdapterSha256"]),
        (trained / "model.safetensors", new["weightsSha256"]),
    ]:
        if checksum(path) != want:
            raise ValueError("artifact checksum mismatch")
    with (
        safe_open(str(previous / "model.safetensors"), framework="pt", device="cpu") as base,
        safe_open(str(trained / "model.safetensors"), framework="pt", device="cpu") as adapted,
    ):
        keys = base.keys()
        if set(keys) != set(adapted.keys()) or any(
            base.get_slice(k).get_shape() != adapted.get_slice(k).get_shape() for k in keys
        ):
            raise ValueError("adapter architecture mismatch")
    shutil.copytree(previous, out)
    filename = "sufficiency-adapter.safetensors"
    shutil.copy2(trained / "model.safetensors", out / filename)
    task = "evidence_sufficient"
    policies = {**old["tasks"], task: new["tasks"][task]}
    metadata = {
        **old,
        "model": out.name,
        "contractVersion": MEMORY_EVIDENCE_CONTRACT,
        "questionsSha256": questions_digest(MEMORY_EVIDENCE_QUESTIONS),
        "tasks": policies,
        "taskAdapters": {
            task: {
                "file": filename,
                "sha256": checksum(out / filename),
                "sourceModel": trained.name,
            }
        },
        "previousBundle": previous.name,
        "promotionStatus": "candidate",
        "promotionReasons": [],
        "promotionEvaluation": None,
        "trainingScope": "isolated_source_aware_sufficiency_with_unchanged_base_and_relevance",
        "dataRows": new["dataRows"],
        "datasetSha256": new["datasetSha256"],
        "trainedAt": new["trainedAt"],
        "trainingSources": new.get("trainingSources"),
        "curriculumSha256": new.get("curriculumSha256"),
        "calibrationOrigins": {
            **old.get("calibrationOrigins", {}),
            task: "sufficiency_adapter_independent_synthetic_holdout",
        },
    }
    old_report = json.loads((previous / "training-report.json").read_text())
    report = json.loads((trained / "training-report.json").read_text())
    for field in ["validation", "test"]:
        report[field] = {
            **{t: v for t, v in old_report[field].items() if t != task},
            task: report[field][task],
        }
    report.update(
        previousBundle=previous.name,
        unchangedBaseVerified=True,
        unchangedRelevanceVerified=True,
        adapterTask=task,
        weightsSha256=old["weightsSha256"],
        pairAdapterSha256=old["pairAdapterSha256"],
        taskAdapters=metadata["taskAdapters"],
        scope="Only sufficiency is newly trained; all other per-task metrics are inherited and unchanged. Frozen worker diagnostics are measured separately.",
    )
    (out / "deployment.json").write_text(json.dumps(metadata, ensure_ascii=False, indent=2))
    (out / "training-report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2))
    return {k: metadata[k] for k in ["model", "weightsSha256", "pairAdapterSha256", "taskAdapters"]}


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    for name in ["previous", "trained", "out"]:
        p.add_argument("--" + name, type=Path, required=True)
    a = p.parse_args()
    print(json.dumps(bundle(a.previous, a.trained, a.out), indent=2))
