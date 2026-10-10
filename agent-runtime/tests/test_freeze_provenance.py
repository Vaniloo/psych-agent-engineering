import hashlib
import json

from psych_agent_runtime.decision_contract import PROFILES
from psych_agent_runtime.routing_service import questions_digest
from training.freeze_deployment import freeze


def test_freeze_uses_curriculum_provenance_without_erasing_rejection(tmp_path):
    checkpoint = tmp_path / "model"
    dataset = tmp_path / "dataset" / "router"
    checkpoint.mkdir()
    dataset.mkdir(parents=True)
    weights = b"fixture-checksum-only"
    (checkpoint / "model.safetensors").write_bytes(weights)
    report = {
        "profile": "router",
        "contractVersion": PROFILES["router"][0],
        "experimental": False,
        "rows": {"train": 1, "calibration": 1, "validation": 1, "test": 1},
    }
    (checkpoint / "training-report.json").write_text(json.dumps(report))
    metadata = {
        "profile": "router",
        "questionsSha256": questions_digest(PROFILES["router"][1]),
        "weightsSha256": hashlib.sha256(weights).hexdigest(),
        "promotionStatus": "rejected",
        "tasks": {
            "route": {"enabled": True, "calibrated": True, "validationScope": "reviewed_held_out"}
        },
    }
    (checkpoint / "deployment.json").write_text(json.dumps(metadata))
    (checkpoint / "rl_agent_config.json").write_text(json.dumps({"temperature": [1.0, 1.0, 1.0]}))
    for name in ["train", "calibration", "validation", "test"]:
        (dataset / (name + ".jsonl")).write_text("{}\n")
    curriculum = {
        "router": {
            "trainingScope": "public_teacher_and_replay",
            "trainSources": {"public_fixture": 1},
        }
    }
    manifest = dataset.parent / "curriculum-manifest.json"
    manifest.write_text(json.dumps(curriculum))
    freeze(checkpoint, dataset, "router")
    result = json.loads((checkpoint / "deployment.json").read_text())
    assert result["trainingScope"] == curriculum["router"]["trainingScope"]
    assert result["trainingSources"] == {"public_fixture": 1}
    assert result["curriculumSha256"] == hashlib.sha256(manifest.read_bytes()).hexdigest()
    assert result["promotionStatus"] == "rejected"
    assert result["tasks"]["route"]["validationScope"] == "synthetic_domain_holdout"
