"""Isolated, gradient-free LoRA weight-editing pilot; never deploys a candidate.

Inspect effective BA changes (not gauge-dependent A/B magnitudes), propose a
small fixed set of edits, select using validation only, then evaluate test once.
NumPy helpers can be tested without Torch; GPU dependencies are loaded in run.
"""

import argparse
import hashlib
import json
import math
import re
import time
from collections import defaultdict
from pathlib import Path

import numpy as np


def sha_file(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def identity(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def case_id(row):
    return row.get("id") or identity([row["task"], row["state"]])


def write(path, value):
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")
    temp.replace(path)


def norm_squared(a, b):
    """||BA||_F^2 without constructing a full dense update matrix."""
    a, b = np.asarray(a, dtype=np.float64), np.asarray(b, dtype=np.float64)
    return float(max(0, np.sum((a @ a.T) * (b.T @ b))))


def difference_squared(a, b, parent_a, parent_b):
    return norm_squared(np.concatenate([a, parent_a]), np.concatenate([b, -parent_b], axis=1))


def principal_change(a, b, parent_a, parent_b):
    """Leading left singular direction of BA-B0A0, via a <=32 square core."""
    left = np.concatenate([b, -parent_b], axis=1).astype(np.float64)
    right = np.concatenate([a, parent_a]).astype(np.float64)
    ql, rl = np.linalg.qr(left, mode="reduced")
    _, rr = np.linalg.qr(right.T, mode="reduced")
    u, s, _ = np.linalg.svd(rl @ rr.T, full_matrices=False)
    if not len(s) or s[0] <= 1e-12:
        raise ValueError("zero effective change")
    return ql @ u[:, 0], float(s[0] ** 2 / np.sum(s ** 2))


def projected_b(b, direction, strength=1.0):
    u = np.asarray(direction, dtype=np.float64)
    if not np.isclose(u @ u, 1):
        raise ValueError("unit direction required")
    return np.asarray(b) - strength * np.outer(u, u @ b)


def ab_key(key):
    return key.replace(".lora_A.", ".lora_B.")


def layer_of(key):
    return int(re.search(r"layers\.(\d+)\.", key).group(1))


def inventory(current, parent):
    if current.keys() != parent.keys():
        raise ValueError("adapter key mismatch")
    rows = []
    for key in sorted(current):
        if ".lora_A." not in key:
            continue
        bk = ab_key(key)
        a, b, a0, b0 = current[key], current[bk], parent[key], parent[bk]
        if a.shape != a0.shape or b.shape != b0.shape:
            raise ValueError("adapter shape mismatch")
        delta = difference_squared(a, b, a0, b0)
        rows.append({"key": key, "layer": layer_of(key),
                     "module": key.split(".")[-3], "changeNormSquared": delta,
                     "parentNormSquared": norm_squared(a0, b0),
                     "shapeA": list(a.shape), "shapeB": list(b.shape)})
    return rows


def make_patch(current, parent, program):
    """Return only changed tensors; sources are immutable throughout the pilot."""
    result = {}
    kind = program["kind"]
    if kind == "original":
        return result
    if kind == "rollback":
        for key, tensor in parent.items():
            if layer_of(key) in program["layers"]:
                result[key] = tensor.copy()
    elif kind == "scale_modules":
        for key, tensor in current.items():
            if ".lora_B." in key and layer_of(key) in program["layers"] and ".mlp." in key:
                result[key] = (tensor * program["factor"]).astype(tensor.dtype)
        if not result:
            raise ValueError("no matching MLP tensors")
    elif kind == "scale":
        for key, tensor in current.items():
            if ".lora_B." in key:
                result[key] = (tensor * program["factor"]).astype(tensor.dtype)
    elif kind in {"principal_projection", "random_projection"}:
        ak = program["key"]
        bk = ab_key(ak)
        a, b = current[ak], current[bk]
        u, _ = principal_change(a, b, parent[ak], parent[bk])
        target = projected_b(b, u, program["strength"]) - b
        if kind == "random_projection":
            rng = np.random.default_rng(program["seed"])
            random_u = b.astype(np.float64) @ rng.normal(size=b.shape[1])
            random_u /= np.linalg.norm(random_u)
            random_delta = projected_b(b, random_u) - b
            target_norm = norm_squared(a, target)
            random_norm = norm_squared(a, random_delta)
            if random_norm <= 1e-20:
                raise ValueError("degenerate random control")
            target = random_delta * math.sqrt(target_norm / random_norm)
        result[bk] = (b + target).astype(b.dtype)
    else:
        raise ValueError("unknown weight program")
    if any(not np.isfinite(v).all() for v in result.values()):
        raise ValueError("nonfinite patch")
    return result


def balanced_sample(rows, per_cell):
    groups = defaultdict(list)
    for row in rows:
        groups[row["task"], row["label"]].append(row)
    result = []
    for cell in sorted(groups):
        result.extend(sorted(groups[cell], key=lambda r: identity([r["task"], r["state"]]))[:per_cell])
    return result


def metrics(rows, margins):
    by_task = {}
    lifecycle = {"write_worthy", "grounded", "duplicate", "supersedes", "conflict"}
    for task in sorted({r["task"] for r in rows}):
        pairs = [(r, x >= 0) for r, x in zip(rows, margins, strict=True) if r["task"] == task]
        by_task[task] = {"rows": len(pairs),
                         "correct": sum(v == (r["label"] == "yes") for r, v in pairs),
                         "falsePositive": sum(v and r["label"] == "no" for r, v in pairs),
                         "falseNegative": sum(not v and r["label"] == "yes" for r, v in pairs)}
    return {"rows": len(rows), "correct": sum(v["correct"] for v in by_task.values()),
            "macroAccuracy": float(np.mean([v["correct"] / v["rows"] for v in by_task.values()])),
            "lifecycleFalsePositive": sum(v["falsePositive"] for k, v in by_task.items() if k in lifecycle),
            "byTask": by_task}


def run(args):
    args.out.mkdir(parents=True, exist_ok=False)
    import os
    for variable, subdir in [("TMPDIR", "tmp"), ("CUDA_CACHE_PATH", "cuda"),
                              ("TORCHINDUCTOR_CACHE_DIR", "inductor"), ("TRITON_CACHE_DIR", "triton")]:
        p = args.out / "cache" / subdir
        p.mkdir(parents=True, exist_ok=True)
        os.environ[variable] = str(p)
    import torch
    from peft import PeftModel
    from safetensors.torch import load_file, save_file
    from transformers import AutoModelForCausalLM, AutoTokenizer
    from psych_agent_runtime.memory_lifecycle_contract import render, INSTRUCTIONS

    started = time.monotonic()
    torch.set_num_threads(4)
    torch.manual_seed(1104)
    report = json.loads((args.current / "training-report.json").read_text())
    parent_report = json.loads((args.parent / "training-report.json").read_text())
    if identity(INSTRUCTIONS) != report["datasetManifest"]["instructionsSha256"]:
        raise ValueError("frozen instructions changed")
    configs = [json.loads((p / "adapter/adapter_config.json").read_text())
               for p in [args.current, args.parent]]
    if any(configs[0][k] != configs[1][k] for k in ["r", "lora_alpha", "use_rslora", "use_dora"]):
        raise ValueError("incompatible adapter scaling")
    if report["contractVersion"] != "psych-memory-lifecycle-v1":
        raise ValueError("expected lifecycle contract")
    if (report["baseRevision"] != parent_report["baseRevision"]
            or args.base.name != report["baseRevision"]):
        raise ValueError("base identity mismatch")
    for checkpoint, metadata in [(args.current, report), (args.parent, parent_report)]:
        if sha_file(checkpoint / "adapter/adapter_model.safetensors") != metadata["adapterSha256"]:
            raise ValueError("adapter hash mismatch")
    if report["initialAdapterSha256"] != parent_report["adapterSha256"]:
        raise ValueError("not the trained parent")
    for name, expected in report["baseWeightsSha256"].items():
        if sha_file(args.base / name) != expected:
            raise ValueError("base weight hash mismatch")
    for split in ["validation", "test"]:
        if sha_file(args.data / (split + ".jsonl")) != report["datasetManifest"]["hashes"][split]:
            raise ValueError("course split hash mismatch")
    current_t = load_file(str(args.current / "adapter/adapter_model.safetensors"))
    parent_t = load_file(str(args.parent / "adapter/adapter_model.safetensors"))
    current = {k: v.float().numpy() for k, v in current_t.items()}
    parent = {k: v.float().numpy() for k, v in parent_t.items()}
    stats = inventory(current, parent)
    layer_norms = defaultdict(float)
    for row in stats:
        layer_norms[row["layer"]] += row["changeNormSquared"]
    layers = sorted(layer_norms, key=lambda k: (-layer_norms[k], k))
    largest = max(stats, key=lambda r: r["changeNormSquared"])
    _, concentration = principal_change(current[largest["key"]], current[ab_key(largest["key"])],
                                         parent[largest["key"]], parent[ab_key(largest["key"])])
    programs = [
        {"id": "original", "kind": "original", "group": "baseline"},
        {"id": "global_scale_080", "kind": "scale", "factor": .8, "group": "control"},
        {"id": "rollback_top1", "kind": "rollback", "layers": layers[:1], "group": "targeted"},
        {"id": "rollback_top2", "kind": "rollback", "layers": layers[:2], "group": "targeted"},
        {"id": "project_top_module_050", "kind": "principal_projection", "key": largest["key"],
         "strength": .5, "group": "targeted"},
        {"id": "project_top_module_100", "kind": "principal_projection", "key": largest["key"],
         "strength": 1., "group": "targeted"},
        {"id": "random_direction_050", "kind": "random_projection", "key": largest["key"],
         "strength": .5, "seed": 1104, "group": "control"},
        {"id": "random_direction_100", "kind": "random_projection", "key": largest["key"],
         "strength": 1., "seed": 1104, "group": "control"},
        {"id": "random_layer", "kind": "rollback", "layers": [sorted(layer_norms)[len(layer_norms)//3]],
         "group": "control"},
        {"id": "random_layers2", "kind": "rollback", "layers": [10, 12], "group": "control"},
    ]
    if args.programs_file:
        programs = json.loads(args.programs_file.read_text())
        if programs[0]["kind"] != "original" or programs[1]["id"] != "global_scale_080":
            raise ValueError("baseline/control ordering mismatch")
        if len({p["id"] for p in programs}) != len(programs):
            raise ValueError("duplicate program IDs")
    dev_all = [json.loads(line) for line in (args.data / "validation.jsonl").read_text().splitlines()]
    dev = balanced_sample(dev_all, args.per_cell)
    protocol = {"status": "frozen_before_first_forward", "sourceSha256": sha_file(Path(__file__)),
                "rendererSha256": identity(INSTRUCTIONS), "programs": programs,
                "currentAdapterSha256": report["adapterSha256"],
                "parentAdapterSha256": parent_report["adapterSha256"],
                "validationSha256": sha_file(args.data / "validation.jsonl"),
                "testSha256": sha_file(args.data / "test.jsonl"), "devRows": len(dev),
                "devSelectionSha256": identity(dev), "gradients": False,
                "testUsedForSelection": False, "deploymentEnabled": False,
                "selection": "targeted only; >= baseline macro/relevance and <= lifecycle FP; max raw correct then fewer changed tensors; baseline if no strict gain",
                "limits": ["programmatic shared-template test, not independent real-user gold",
                           "weight norm is not semantic attribution", "original policies not recalibrated",
                           "pilot not an original-paper reproduction"]}
    prior_selection = None
    if args.selection_from:
        prior_protocol = json.loads(args.selection_from.with_name("protocol.json").read_text())
        for field in ["programs", "currentAdapterSha256", "parentAdapterSha256", "rendererSha256",
                      "validationSha256", "testSha256", "devSelectionSha256", "selection"]:
            if protocol[field] != prior_protocol[field]:
                raise ValueError("evaluation resume protocol changed: " + field)
        prior_selection = json.loads(args.selection_from.read_text())
        if not prior_selection["frozenBeforeTest"]:
            raise ValueError("selection must be frozen before test")
        protocol["resumedSelectionSha256"] = sha_file(args.selection_from)
        protocol["originalProtocolSha256"] = sha_file(args.selection_from.with_name("protocol.json"))
    if args.challenge_file:
        if not prior_selection:
            raise ValueError("additional challenge requires already frozen selection")
        if sha_file(args.challenge_file) != report["challengeSha256"]:
            raise ValueError("challenge is not the frozen training-report challenge")
        protocol["additionalChallengeSha256"] = sha_file(args.challenge_file)
        protocol["evaluationScope"] = "frozen_development_challenge_not_selection"
    write(args.out / "protocol.json", protocol)
    write(args.out / "weight-inspection.json", {"modules": stats, "layerChangeSquared": dict(layer_norms),
                                                 "largestModulePrincipalEnergy": concentration})
    free, _ = torch.cuda.mem_get_info()
    if free < 24 * 1024**3:
        raise RuntimeError("need at least 24 GiB free; do not evict other processes")
    torch.cuda.set_per_process_memory_fraction(.24)
    tokenizer = AutoTokenizer.from_pretrained(args.base, padding_side="left", local_files_only=True)
    base = AutoModelForCausalLM.from_pretrained(args.base, torch_dtype=torch.bfloat16,
                attn_implementation="sdpa", local_files_only=True).to("cuda:0")
    model = PeftModel.from_pretrained(base, args.current / "adapter").eval()
    model.requires_grad_(False)
    parameters = dict(model.named_parameters())
    runtime_keys = {k: k.replace(".lora_A.", ".lora_A.default.").replace(".lora_B.", ".lora_B.default.")
                    for k in current_t}
    if any(k not in parameters for k in runtime_keys.values()):
        raise ValueError("runtime LoRA key alignment mismatch")
    no, yes = [tokenizer.convert_tokens_to_ids(x) for x in ("no", "yes")]
    forward_batches = 0

    def install(program):
        patch = make_patch(current, parent, program)
        with torch.no_grad():
            for key, name in runtime_keys.items():
                source = torch.from_numpy(patch[key]) if key in patch else current_t[key]
                parameters[name].copy_(source.to(device=parameters[name].device, dtype=parameters[name].dtype))
        return patch

    def score(rows):
        nonlocal forward_batches
        values = []
        with torch.inference_mode():
            for start in range(0, len(rows), args.batch_size):
                batch = rows[start:start+args.batch_size]
                encoded = tokenizer([render(r["task"], r["state"]) for r in batch], padding=True,
                                    return_tensors="pt", truncation=False)
                if encoded.input_ids.shape[1] > 2048:
                    raise ValueError("no silent truncation")
                logits = model(**encoded.to("cuda:0"), use_cache=False, logits_to_keep=1).logits[:, -1, [no, yes]].float()
                values.extend((logits[:, 1]-logits[:, 0]).cpu().tolist())
                forward_batches += 1
        if not all(math.isfinite(v) for v in values):
            raise ValueError("nonfinite score")
        return values

    development = prior_selection["development"] if prior_selection else []
    for program in ([] if prior_selection else programs):
        patch = install(program)
        margins = score(dev)
        result = {"program": program, "metrics": metrics(dev, margins),
                  "changedTensors": len(patch),
                  "changedEntries": sum(int(np.count_nonzero(v != current[k])) for k, v in patch.items())}
        development.append(result)
        write(args.out / (program["id"] + "-dev.json"), {**result, "margins": margins})
        print(json.dumps({"stage": "development", **result}, ensure_ascii=False), flush=True)
        write(args.out / "progress.json", {"stage": "development", "programsCompleted": len(development)})
    original = development[0]["metrics"]
    eligible = [d for d in development if d["program"]["group"] == "targeted"
                and d["metrics"]["macroAccuracy"] >= original["macroAccuracy"]
                and d["metrics"]["lifecycleFalsePositive"] <= original["lifecycleFalsePositive"]
                and d["metrics"]["byTask"]["relevance"]["correct"] >= original["byTask"]["relevance"]["correct"]
                and d["metrics"]["correct"] > original["correct"]]
    diagnostic = max((d for d in development if d["program"]["group"] == "targeted"),
                     key=lambda d: (d["metrics"]["correct"], -d["changedTensors"]))
    selected = max(eligible, key=lambda d: (d["metrics"]["correct"], -d["changedTensors"])) if eligible else diagnostic
    selection = {"program": selected["program"], "passedDevGate": bool(eligible),
                 "purpose": "candidate" if eligible else "diagnostic_only_no_promotion",
                 "frozenBeforeTest": True, "development": development}
    if prior_selection is not None and selection != prior_selection:
        raise ValueError("cannot change frozen selection while resuming evaluation")
    write(args.out / "selection.json", selection)
    # Test is opened only after the selection decision has been saved.
    if args.challenge_file:
        heldout = []
        for line in args.challenge_file.read_text().splitlines():
            case = json.loads(line)
            if len(case["expected"]) != 1:
                raise ValueError("one task per challenge decision required")
            task, label = next(iter(case["expected"].items()))
            heldout.append({**case, "task": task, "label": label})
    else:
        heldout = [json.loads(line) for line in (args.data / "test.jsonl").read_text().splitlines()]
    dev_keys = {identity([r["task"], r["state"]]) for r in dev_all}
    if any(identity([r["task"], r["state"]]) in dev_keys for r in heldout):
        raise ValueError("validation/test exact input overlap")
    final_programs = [programs[0], selected["program"], programs[1]]
    chosen = selected["program"]
    control_id = ("random_layer" if len(chosen.get("layers", [])) == 1 else "random_layers2") if chosen["kind"] == "rollback" else (
        "random_direction_050" if chosen.get("strength") == .5 else "random_direction_100")
    control_id = chosen.get("controlId", control_id)
    control = next(p for p in programs if p["id"] == control_id)
    final_programs.append(control)
    final = []
    for program in final_programs:
        install(program)
        margins = score(heldout)
        result = {"program": program, "metrics": metrics(heldout, margins)}
        final.append(result)
        write(args.out / (program["id"] + "-test.json"), {**result, "cases": [
            {"id": case_id(r), "task": r["task"], "label": r["label"], "margin": m}
            for r, m in zip(heldout, margins, strict=True)]})
        print(json.dumps({"stage": "test", **result}, ensure_ascii=False), flush=True)
    patch = make_patch(current, parent, selected["program"])
    destination = args.out / "candidate-adapter"
    destination.mkdir()
    saved = {k: (torch.from_numpy(patch[k]).to(v.dtype) if k in patch else v).contiguous()
             for k, v in current_t.items()}
    save_file(saved, str(destination / "adapter_model.safetensors"))
    (destination / "adapter_config.json").write_bytes((args.current / "adapter/adapter_config.json").read_bytes())
    candidate_sha = sha_file(destination / "adapter_model.safetensors")
    # Verify serialization in CPU tensors, then a small actual reloaded forward.
    reloaded = load_file(str(destination / "adapter_model.safetensors"))
    if any(not torch.equal(saved[k], reloaded[k]) for k in saved):
        raise ValueError("serialized tensor mismatch")
    install(selected["program"])
    before = score(dev[:8])
    with torch.no_grad():
        for k, name in runtime_keys.items():
            parameters[name].copy_(reloaded[k].to(parameters[name].device, dtype=parameters[name].dtype))
    after = score(dev[:8])
    reload_delta = max(abs(a-b) for a,b in zip(before, after, strict=True))
    unchanged = all(sha_file(p / "adapter/adapter_model.safetensors") == r["adapterSha256"]
                    for p,r in [(args.current, report),(args.parent,parent_report)])
    result = {"status": "complete", "selection": {k:v for k,v in selection.items() if k != "development"},
              "development": development, "test": final, "candidateAdapterSha256": candidate_sha,
              "sourceAdaptersUnchanged": unchanged, "reloadMaxLogitDelta": reload_delta,
              "forwardBatches": forward_batches, "totalSeconds": time.monotonic()-started,
              "peakAllocatedMiB": torch.cuda.max_memory_allocated()/1024**2,
              "gradientsUsed": False, "deploymentEnabled": False,
              "calibrationValidForEditedAdapter": False}
    result["evaluationScope"] = "frozen_development_challenge" if args.challenge_file else "course_test"
    if not unchanged or reload_delta > .005:
        raise ValueError("source preservation or reload check failed")
    write(args.out / "results.json", result)
    write(args.out / "progress.json", {"stage": "complete", "deploymentEnabled": False})
    print(json.dumps({"stage":"complete", "totalSeconds":result["totalSeconds"]}), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ["base", "current", "parent", "data", "out"]:
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--programs-file", type=Path)
    parser.add_argument("--per-cell", type=int, default=16)
    parser.add_argument("--batch-size", type=int, default=16)
    parser.add_argument("--selection-from", type=Path,
                        help="Resume evaluation from frozen selection, without repeating development")
    parser.add_argument("--challenge-file", type=Path,
                        help="Additional frozen challenge diagnostic only; requires --selection-from")
    args = parser.parse_args()
    output_existed = args.out.exists()
    try:
        run(args)
    except Exception as exc:
        if not output_existed and args.out.is_dir():
            write(args.out / "failure.json", {"type": type(exc).__name__, "message": str(exc)[:300]})
        raise
