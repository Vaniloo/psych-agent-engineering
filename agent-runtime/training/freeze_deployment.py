"""Seal evaluated weights and dataset provenance before starting decision workers."""

import argparse
import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

from psych_agent_runtime.decision_contract import PROFILES
from psych_agent_runtime.routing_service import questions_digest


def freeze(checkpoint: Path, dataset: Path, profile: str):
    report = json.loads((checkpoint / "training-report.json").read_text())
    metadata = json.loads((checkpoint / "deployment.json").read_text())
    contract, questions = PROFILES[profile]
    if (
        report["profile"] != profile
        or report["contractVersion"] != contract
        or metadata["questionsSha256"] != questions_digest(questions)
    ):
        raise ValueError("artifact contract mismatch")
    with (checkpoint / "model.safetensors").open("rb") as stream:
        checksum = hashlib.file_digest(stream, "sha256").hexdigest()
    if checksum != metadata["weightsSha256"]:
        raise ValueError("checkpoint modified after evaluation")
    config = json.loads((checkpoint / "rl_agent_config.json").read_text())
    if config.get("temperature") != [1.0, 1.0, 1.0] or config.get("temperature_by_options"):
        raise ValueError("raw checkpoint must remain neutral; deployment applies task temperature")
    source_hashes = {
        name: hashlib.sha256((dataset / f"{name}.jsonl").read_bytes()).hexdigest()
        for name in ["train", "calibration", "validation", "test"]
    }
    metadata.update(
        model=checkpoint.name,
        trainedAt=datetime.fromtimestamp(
            (checkpoint / "training-report.json").stat().st_mtime, UTC
        ).isoformat(),
        trainingScope=(
            "domain_synthetic_holdouts"
            if profile == "executor"
            else "domain_synthetic_holdouts_public_teacher_training"
        ),
        dataRows=report["rows"],
        datasetSha256=source_hashes,
    )
    for policy in metadata["tasks"].values():
        if policy["enabled"] and (report["experimental"] or not policy["calibrated"]):
            raise ValueError("unvalidated task cannot be promoted")
        if policy.get("validationScope") == "reviewed_held_out":
            policy["validationScope"] = "synthetic_domain_holdout"
    (checkpoint / "deployment.json").write_text(json.dumps(metadata, ensure_ascii=False, indent=2))
    return {
        "profile": profile,
        "model": metadata["model"],
        "enabledTasks": [t for t, p in metadata["tasks"].items() if p["enabled"]],
        "weightsSha256": checksum,
        "rows": report["rows"],
    }


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--checkpoint", type=Path, required=True)
    p.add_argument("--dataset", type=Path, required=True)
    p.add_argument("--profile", choices=PROFILES, required=True)
    a = p.parse_args()
    print(json.dumps(freeze(a.checkpoint, a.dataset, a.profile), indent=2))
