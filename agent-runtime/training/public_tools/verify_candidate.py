"""Reload a finished public adapter and compare existing validation scores; no new gold."""

import argparse
import json
from pathlib import Path

from .course import load
from .fetch import sha256_file, write_json
from .train import encode


def verify(data, preflight, candidate, out):
    report = json.loads((candidate / "training-report.json").read_text())
    plan = json.loads((preflight / "preflight.json").read_text())
    adapter = candidate / "adapter"
    if sha256_file(adapter / "adapter_model.safetensors") != report["adapterSha256"]:
        raise ValueError("saved_adapter_checksum_mismatch")
    _, parts = load(data)
    cases = json.loads((candidate / "validation.json").read_text())["cases"]
    # Verification cases are selected from already evaluated validation, never authored.
    chosen = []
    for action in ("tool_call", "assistant_text"):
        chosen.extend([r for r in cases if r["observedAction"] == action][:8])
    if not chosen:
        raise ValueError("no_validation_scores_for_reload_verification")
    by_id = {r["id"]: r for r in parts["validation"]}
    import torch
    from peft import PeftModel
    from transformers import AutoModelForCausalLM, AutoTokenizer

    torch.set_num_threads(4)
    torch.cuda.set_per_process_memory_fraction(0.24)
    tokenizer = AutoTokenizer.from_pretrained(plan["base"], padding_side="left", local_files_only=True)
    base = AutoModelForCausalLM.from_pretrained(plan["base"], local_files_only=True,
        torch_dtype=torch.bfloat16, attn_implementation="sdpa").to("cuda:0")
    model = PeftModel.from_pretrained(base, adapter).eval()
    no, yes = [tokenizer.convert_tokens_to_ids(t) for t in ("no", "yes")]
    comparisons = []
    with torch.inference_mode():
        for original in chosen:
            row = by_id[original["id"]]
            tokens, audit = encode(tokenizer, row, plan["maxTokens"], plan["maxPaddedTokens"])
            if audit["excludedReason"]:
                raise ValueError("verification_row_budget_changed")
            values = [{k: v[i] for k, v in tokens.items()} for i in range(len(row["candidateTools"]))]
            batch = tokenizer.pad(values, padding=True, return_tensors="pt").to("cuda:0")
            logits = model(**batch, use_cache=False, logits_to_keep=1).logits[:, -1, [no, yes]].float()
            scores = (logits[:, 1] - logits[:, 0]).cpu().tolist()
            delta = max(abs(a - b) for a, b in zip(scores, original["scores"], strict=True))
            before = max(range(len(scores)), key=original["scores"].__getitem__)
            after = max(range(len(scores)), key=scores.__getitem__)
            comparisons.append({"id": row["id"], "maxScoreDelta": delta, "sameChoice": before == after})
    passed = all(r["sameChoice"] and r["maxScoreDelta"] <= 0.005 for r in comparisons)
    result = {"status": "passed" if passed else "failed", "rows": len(comparisons),
              "scope": "disk_reload_consistency_not_model_quality", "comparisons": comparisons,
              "adapterSha256": report["adapterSha256"], "deploymentEnabled": False}
    write_json(out, result)
    if not passed:
        raise ValueError("disk_reload_verification_failed")
    return result


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    for name in ("data", "preflight", "candidate", "out"):
        p.add_argument("--" + name, type=Path, required=True)
    a = p.parse_args()
    print(json.dumps(verify(a.data, a.preflight, a.candidate, a.out)))
