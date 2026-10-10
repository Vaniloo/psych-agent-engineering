"""Bundle task-specific parameters with unchanged legacy Memory weights.

Head-only mode verifies a frozen encoder. Full-adapter mode swaps the complete
trained parameters only for relevance, restoring the base for all other tasks.
"""

import argparse
import hashlib
import json
import shutil
from pathlib import Path

from psych_agent_runtime.decision_contract import MEMORY_PAIR_CONTRACT, MEMORY_PAIR_QUESTIONS
from psych_agent_runtime.routing_service import questions_digest


def checksum(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def bundle(base, trained, out, full=False):
    import torch
    from safetensors import safe_open
    from safetensors.torch import save_file

    if out.exists():
        raise ValueError("refuse to overwrite a Memory bundle")
    baseline = json.loads((base / "deployment.json").read_text())
    metadata = json.loads((trained / "deployment.json").read_text())
    if baseline["profile"] != "memory" or metadata["contractVersion"] != MEMORY_PAIR_CONTRACT:
        raise ValueError("memory adapter contract mismatch")
    if checksum(base / "model.safetensors") != baseline["weightsSha256"]:
        raise ValueError("base artifact checksum mismatch")
    if checksum(trained / "model.safetensors") != metadata["weightsSha256"]:
        raise ValueError("trained artifact checksum mismatch")
    with (
        safe_open(str(base / "model.safetensors"), framework="pt", device="cpu") as original,
        safe_open(str(trained / "model.safetensors"), framework="pt", device="cpu") as adapted,
    ):
        if set(original.keys()) != set(adapted.keys()):
            raise ValueError("adapter architecture changed")
        heads = {}
        keys = original.keys()
        for key in keys:
            if original.get_tensor(key).shape != adapted.get_tensor(key).shape:
                raise ValueError("adapter tensor shape changed")
            if key.startswith("encoder.") and not full:
                if not torch.equal(original.get_tensor(key), adapted.get_tensor(key)):
                    raise ValueError("adapter changed frozen encoder: " + key)
            else:
                heads[key] = adapted.get_tensor(key)
    shutil.copytree(base, out)
    adapter_file = "pair-adapter.safetensors" if full else "pair-head.safetensors"
    checksum_key = "pairAdapterSha256" if full else "pairHeadSha256"
    save_file(heads, str(out / adapter_file))
    tasks = {t: baseline["tasks"][t] for t in MEMORY_PAIR_QUESTIONS if t != "relevance"}
    tasks["relevance"] = metadata["tasks"]["relevance"]
    metadata.update(
        model=out.name,
        profile="memory",
        contractVersion=MEMORY_PAIR_CONTRACT,
        questionsSha256=questions_digest(MEMORY_PAIR_QUESTIONS),
        weightsSha256=baseline["weightsSha256"],
        tasks=tasks,
        baseModel=baseline["model"],
        baseWeightsSha256=baseline["weightsSha256"],
        baseDataRows=baseline.get("dataRows"),
        **{checksum_key: checksum(out / adapter_file)},
        frozenEncoderVerified=not full,
        unchangedBaseVerified=True,
        promotionStatus="candidate",
        calibrationOrigins={
            t: "unchanged_base_policy"
            if t != "relevance"
            else "pair_adapter_independent_synthetic_holdout"
            for t in tasks
        },
    )
    legacy_report = json.loads((base / "training-report.json").read_text())
    report = json.loads((trained / "training-report.json").read_text())
    for field in ["validation", "test"]:
        report[field] = {
            **{t: legacy_report[field][t] for t in tasks if t != "relevance"},
            "relevance": report[field]["relevance"],
        }
    report.update(
        adapterOnly=True,
        frozenEncoderVerified=not full,
        unchangedBaseVerified=True,
        baseModel=baseline["model"],
        baseWeightsSha256=baseline["weightsSha256"],
        **{checksum_key: metadata[checksum_key]},
        scope="Legacy per-task scores are inherited from byte-identical base weights, not remeasured adapter training scores",
    )
    (out / "deployment.json").write_text(json.dumps(metadata, ensure_ascii=False, indent=2))
    (out / "training-report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2))
    return {
        "model": out.name,
        "weightsSha256": metadata["weightsSha256"],
        checksum_key: metadata[checksum_key],
        "frozenEncoderVerified": not full,
        "unchangedBaseVerified": True,
    }


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--base", type=Path, required=True)
    p.add_argument("--trained", type=Path, required=True)
    p.add_argument("--out", type=Path, required=True)
    p.add_argument(
        "--full-adapter",
        action="store_true",
        help="Task-specific full parameters with the untouched base restored for other tasks",
    )
    a = p.parse_args()
    print(json.dumps(bundle(a.base, a.trained, a.out, a.full_adapter)), flush=True)
