"""Bounded public-demonstration LoRA experiment; no absolute calibration or deployment."""

import argparse
import hashlib
import json
import math
import random
import time
from collections import Counter
from pathlib import Path

from .contract import loss, metrics, render, specification
from .convert import digest
from .course import CONTRACT, SPLITS, load
from .fetch import sha256_file, write_json


def fingerprint():
    return {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in Path(__file__).parent.glob("*.py")
            if not p.name.startswith(".")}


def checkpoint(torch, model, optimizer, tokenizer, destination, cursor, identity):
    """Write only this experiment's state, atomically; never overwrite checkpoints."""
    if destination.exists():
        raise ValueError("refuse_overwriting_checkpoint")
    staging = destination.with_name(destination.name + ".partial")
    staging.mkdir(parents=True, exist_ok=False)
    model.save_pretrained(staging / "adapter", safe_serialization=True)
    tokenizer.save_pretrained(staging / "adapter")
    torch.save({"optimizer": optimizer.state_dict(), "torchRng": torch.get_rng_state(),
                "cudaRng": torch.cuda.get_rng_state_all(), "cursor": cursor}, staging / "state.pt")
    hashes = {}
    for p in staging.rglob("*"):
        if p.is_file():
            hashes[str(p.relative_to(staging))] = sha256_file(p)
    write_json(staging / "checkpoint.json", {"identity": identity, "cursor": cursor, "artifactSha256": hashes})
    staging.rename(destination)


def checkpoint_state(torch, folder, identity):
    manifest = json.loads((folder / "checkpoint.json").read_text())
    if manifest["identity"] != identity:
        raise ValueError("checkpoint_training_identity_mismatch")
    for name, sha in manifest["artifactSha256"].items():
        if Path(name).is_absolute() or ".." in Path(name).parts:
            raise ValueError("invalid_checkpoint_path")
        if sha256_file(folder / name) != sha:
            raise ValueError("checkpoint_checksum_mismatch")
    state = torch.load(folder / "state.pt", map_location="cpu", weights_only=True)
    if state["cursor"] != manifest["cursor"]:
        raise ValueError("checkpoint_cursor_mismatch")
    return state


def encode(tokenizer, row, max_tokens, max_padded_tokens):
    result = tokenizer([render(row, i) for i in range(len(row["candidateTools"]))],
                       add_special_tokens=False, padding=False, truncation=False)
    lengths = [len(t) for t in result["input_ids"]]
    longest = max(lengths)
    reason = ("context_budget" if longest > max_tokens else
              "candidate_batch_budget" if longest * len(lengths) > max_padded_tokens else None)
    return result, {"maxTokens": longest, "candidateCount": len(lengths),
                    "paddedTokens": longest * len(lengths), "excludedReason": reason}


def prepare(args):
    if args.out.exists():
        raise ValueError("refuse_overwriting_preflight")
    manifest, parts = load(args.data)
    if manifest["sourcePolicy"] != "primary_apache":
        raise ValueError("this_experiment_only_trains_primary_apache_source")
    from transformers import AutoTokenizer
    tokenizer = AutoTokenizer.from_pretrained(args.base, padding_side="left", local_files_only=True)
    args.out.mkdir(parents=True)
    selected, counts, exclusions, total = {}, {}, [], 0
    for split in SPLITS:
        accepted = []
        budgets = Counter()
        for index, row in enumerate(parts[split]):
            if not row["informativeChoice"]:
                reason = "single_action_only"
                detail = {}
            else:
                _, detail = encode(tokenizer, row, args.max_tokens, args.max_padded_tokens)
                reason = detail["excludedReason"]
            if reason:
                exclusions.append({"id": row["id"], "split": split, "reason": reason, **detail})
                budgets[reason] += 1
            else:
                accepted.append(row)
            total += 1
            if total % 100 == 0:
                write_json(args.out / "progress.json", {"status": "token_preflight", "rowsChecked": total,
                                                        "totalRows": sum(map(len, parts.values()))})
        accepted.sort(key=lambda r: digest([args.seed, r["id"]]))
        limit = args.max_train_rows if split == "train" else args.eval_rows
        chosen = accepted[:limit] if limit else accepted
        selected[split] = [r["id"] for r in chosen]
        counts[split] = {"sourceRows": len(parts[split]), "budgetEligibleRows": len(accepted),
                         "selectedRows": len(chosen), "excludedReasons": dict(budgets),
                         "selectedActions": dict(Counter(r["observedAction"] for r in chosen))}
        if not chosen:
            raise ValueError("empty_budget_eligible_split")
    write_json(args.out / "selected.json", selected)
    write_json(args.out / "exclusions.json", exclusions)
    report = {"status": "preflight_complete", "contract": CONTRACT, "courseManifestSha256": digest(manifest),
              "base": str(args.base.resolve()), "rendererSpec": specification(), "seed": args.seed,
              "maxTokens": args.max_tokens, "maxPaddedTokens": args.max_padded_tokens,
              "counts": counts, "selectedSha256": digest(selected), "sourceSha256": fingerprint(),
              "truncatedRows": 0, "trainingStarted": False, "deploymentEnabled": False,
              "teacherApiCalled": False, "selection": "deterministic_hash_sample_without_new_text_or_labels"}
    write_json(args.out / "preflight.json", report)
    write_json(args.out / "progress.json", {"status": "preflight_complete"})
    return report


def run(args):
    invoked = time.monotonic()
    if args.out.exists():
        raise ValueError("refuse_overwriting_training_output")
    manifest, parts = load(args.data)
    if manifest["sourcePolicy"] != "primary_apache":
        raise ValueError("this_experiment_only_trains_primary_apache_source")
    plan = json.loads((args.preflight / "preflight.json").read_text())
    selected = json.loads((args.preflight / "selected.json").read_text())
    if (plan["courseManifestSha256"] != digest(manifest) or plan["selectedSha256"] != digest(selected)
            or plan["rendererSpec"] != specification() or plan["sourceSha256"] != fingerprint()
            or plan["base"] != str(args.base.resolve()) or plan["contract"] != CONTRACT):
        raise ValueError("frozen_training_plan_mismatch")
    for split in SPLITS:
        by_id = {r["id"]: r for r in parts[split]}
        if len(set(selected[split])) != len(selected[split]):
            raise ValueError("duplicate_preflight_selection")
        parts[split] = [by_id[i] for i in selected[split]]
        if not parts[split]:
            raise ValueError("empty_training_plan_split")
    import torch
    from peft import LoraConfig, get_peft_model, set_peft_model_state_dict
    from transformers import AutoModelForCausalLM, AutoTokenizer

    torch.set_num_threads(4)
    torch.manual_seed(args.seed)
    torch.cuda.set_per_process_memory_fraction(args.memory_fraction)
    free, total = torch.cuda.mem_get_info()
    if free < total * args.memory_fraction + 8 * 1024**3:
        raise ValueError("insufficient_gpu_headroom_no_other_processes_modified")
    base_report = json.loads(args.base_report.read_text())
    if args.base.name != base_report["revision"]:
        raise ValueError("base_revision_mismatch")
    for filename, sha in base_report["weightsSha256"].items():
        if sha256_file(args.base / filename) != sha:
            raise ValueError("base_weight_checksum_mismatch")
    args.out.mkdir(parents=True)
    started = invoked
    deadline = started + args.max_wall_seconds if args.max_wall_seconds else float("inf")
    train_deadline = deadline - args.evaluation_reserve_seconds
    write_json(args.out / "progress.json", {"status": "loading_base"})
    tokenizer = AutoTokenizer.from_pretrained(args.base, padding_side="left", local_files_only=True)
    no, yes = [tokenizer.convert_tokens_to_ids(t) for t in ("no", "yes")]
    if any(tokenizer.encode(t, add_special_tokens=False) != [v] for t, v in (("no", no), ("yes", yes))):
        raise ValueError("non_atomic_scoring_tokens")
    encoded = {}
    for rows in parts.values():
        for row in rows:
            tokens, detail = encode(tokenizer, row, plan["maxTokens"], plan["maxPaddedTokens"])
            if detail["excludedReason"]:
                raise ValueError("frozen_row_exceeds_token_budget")
            encoded[row["id"]] = [{k: v[i] for k, v in tokens.items()}
                                   for i in range(len(row["candidateTools"]))]
    model = AutoModelForCausalLM.from_pretrained(args.base, local_files_only=True,
        torch_dtype=torch.bfloat16, attn_implementation="sdpa").to("cuda:0")

    def score(row):
        tokens = tokenizer.pad(encoded[row["id"]], padding=True, return_tensors="pt").to("cuda:0")
        logits = model(**tokens, use_cache=False, logits_to_keep=1).logits[:, -1, [no, yes]].float()
        values = logits[:, 1] - logits[:, 0]
        if not torch.isfinite(values).all():
            raise ValueError("nonfinite_scores")
        return values

    def evaluate(rows, stage):
        model.eval()
        results = []
        with torch.inference_mode():
            for i, row in enumerate(rows):
                if time.monotonic() >= deadline:
                    break
                results.append({**row, "scores": score(row).cpu().tolist()})
                if (i + 1) % 20 == 0:
                    write_json(args.out / "progress.json", {"status": stage, "rows": i + 1, "total": len(rows)})
        summary = metrics(results)
        summary.update(expectedRows=len(rows), evaluatedRows=len(results), complete=len(results) == len(rows))
        write_json(args.out / (stage + ".json"), {"metrics": summary, "cases": [
            {k: r[k] for k in ("id", "source", "sourceTurn", "observedAction", "targetIndices", "scores")}
            for r in results]})
        return summary

    baseline = evaluate(parts["test"], "base_test")
    model = get_peft_model(model, LoraConfig(r=16, lora_alpha=32, lora_dropout=0.05, bias="none",
        task_type="CAUSAL_LM", target_modules=["q_proj", "k_proj", "v_proj", "o_proj", "gate_proj", "up_proj", "down_proj"]))
    model.gradient_checkpointing_enable(gradient_checkpointing_kwargs={"use_reentrant": False})
    model.enable_input_require_grads()
    trainable = [p for p in model.parameters() if p.requires_grad]
    optimizer = torch.optim.AdamW(trainable, lr=args.learning_rate, weight_decay=0.01)
    identity = digest({"course": digest(manifest), "preflight": digest(plan), "source": fingerprint(),
                       "base": base_report["weightsSha256"], "epochs": args.epochs, "seed": args.seed,
                       "lr": args.learning_rate, "accumulation": args.accumulation})
    report = {"status": "training", "contract": CONTRACT, "baseModel": base_report["model"],
              "baseRevision": base_report["revision"], "baseWeightsSha256": base_report["weightsSha256"],
              "rendererSpec": specification(), "preflightSha256": digest(plan),
              "courseManifestSha256": digest(manifest), "sourceSha256": fingerprint(),
              "epochs": args.epochs, "learningRate": args.learning_rate, "seed": args.seed,
              "gradientAccumulation": args.accumulation, "trainableParameters": sum(p.numel() for p in trainable),
              "rowsBySplit": {s: len(v) for s, v in parts.items()}, "baseTest": baseline,
              "method": "BF16 LoRA r16 alpha32; observed-action CE only; no absolute utility BCE or finish/clarify gold",
              "deploymentEnabled": False, "formalModelsChanged": False, "teacherApiCalled": False,
              "maxWallSeconds": args.max_wall_seconds, "evaluationReserveSeconds": args.evaluation_reserve_seconds,
              "checkpointEveryOptimizerSteps": args.checkpoint_every_steps, "trainingIdentity": identity}
    write_json(args.out / "training-report.json", report)
    steps, seen, loss_sum = 0, 0, 0.0
    start_epoch, next_row = 0, 0
    if args.resume_from:
        from safetensors.torch import load_file
        saved = checkpoint_state(torch, args.resume_from, identity)
        outcome = set_peft_model_state_dict(model, load_file(str(args.resume_from / "adapter/adapter_model.safetensors")))
        if outcome.unexpected_keys or any("lora_" in k for k in outcome.missing_keys):
            raise ValueError("checkpoint_adapter_state_mismatch")
        optimizer.load_state_dict(saved["optimizer"])
        torch.set_rng_state(saved["torchRng"])
        torch.cuda.set_rng_state_all(saved["cudaRng"])
        cursor = saved["cursor"]
        start_epoch, next_row = cursor["epoch"], cursor["nextRow"]
        seen, steps, loss_sum = cursor["seen"], cursor["steps"], cursor["lossSum"]
        report["resumedFrom"] = str(args.resume_from)
    total_states = len(parts["train"]) * args.epochs
    total_steps = math.ceil(len(parts["train"]) / args.accumulation) * args.epochs
    begin = time.monotonic()
    optimizer.zero_grad()
    budget_stopped = False
    last_checkpoint = None
    for epoch in range(start_epoch, args.epochs):
        rows = list(parts["train"])
        random.Random(args.seed + epoch).shuffle(rows)
        model.train()
        for i, row in enumerate(rows):
            if epoch == start_epoch and i < next_row:
                continue
            value = loss(torch, score(row), row)
            if not torch.isfinite(value):
                raise ValueError("nonfinite_loss")
            window = min(args.accumulation, len(rows) - (i // args.accumulation) * args.accumulation)
            (value / window).backward()
            seen += 1
            loss_sum += float(value.detach())
            if (i + 1) % args.accumulation == 0 or i + 1 == len(rows):
                torch.nn.utils.clip_grad_norm_(trainable, 1)
                steps += 1
                rate = args.learning_rate * min(1, steps / 10) * max(0.1, 1 - steps / total_steps)
                for group in optimizer.param_groups:
                    group["lr"] = rate
                optimizer.step()
                optimizer.zero_grad()
                cursor = {"epoch": epoch, "nextRow": i + 1, "seen": seen, "steps": steps, "lossSum": loss_sum}
                if args.checkpoint_every_steps and steps % args.checkpoint_every_steps == 0:
                    last_checkpoint = args.out / "checkpoints" / f"step-{steps:06d}"
                    checkpoint(torch, model, optimizer, tokenizer, last_checkpoint, cursor, identity)
                    write_json(args.out / "last-checkpoint.json", {"path": str(last_checkpoint), **cursor})
                if time.monotonic() >= train_deadline:
                    budget_stopped = True
            if seen % 10 == 0 or i + 1 == len(rows):
                elapsed = time.monotonic() - begin
                progress = {"status": "training", "statesCompleted": seen, "totalStates": total_states,
                            "optimizerSteps": steps, "meanLoss": loss_sum / seen, "elapsedSeconds": elapsed,
                            "estimatedRemainingSeconds": elapsed * (total_states - seen) / max(1, seen),
                            "maxWallSeconds": args.max_wall_seconds}
                write_json(args.out / "progress.json", progress)
                print(json.dumps(progress), flush=True)
            if budget_stopped:
                break
        if budget_stopped:
            break
    cursor = {"epoch": epoch, "nextRow": i + 1, "seen": seen, "steps": steps, "lossSum": loss_sum}
    if last_checkpoint is None or json.loads((last_checkpoint / "checkpoint.json").read_text())["cursor"] != cursor:
        last_checkpoint = args.out / "checkpoints" / f"step-{steps:06d}-final"
        checkpoint(torch, model, optimizer, tokenizer, last_checkpoint, cursor, identity)
        write_json(args.out / "last-checkpoint.json", {"path": str(last_checkpoint), **cursor})
    model.save_pretrained(args.out / "adapter", safe_serialization=True)
    tokenizer.save_pretrained(args.out / "adapter")
    optimization_seconds = time.monotonic() - begin
    model.gradient_checkpointing_disable()
    validation = evaluate(parts["validation"], "validation")
    test = evaluate(parts["test"], "test")
    fully_trained = seen == total_states
    report.update(status="complete" if fully_trained and validation["complete"] and test["complete"] else "completed_with_budget_limit",
                  trainingStatesSeen=seen, optimizerSteps=steps, plannedTrainingStates=total_states,
                  fullTrainingCompleted=fully_trained, budgetStopped=budget_stopped,
                  meanLoss=loss_sum / seen, optimizationSeconds=optimization_seconds,
                  totalSeconds=time.monotonic() - started, validation=validation, test=test,
                  peakAllocatedMiB=torch.cuda.max_memory_allocated() / 1024**2,
                  adapterSha256=hashlib.sha256((args.out / "adapter/adapter_model.safetensors").read_bytes()).hexdigest())
    write_json(args.out / "training-report.json", report)
    write_json(args.out / "progress.json", {"status": report["status"], "deploymentEnabled": False,
                                            "trainingStatesSeen": seen, "fullTrainingCompleted": fully_trained})
    return report


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    for name in ("data", "base", "out"):
        p.add_argument("--" + name, type=Path, required=True)
    p.add_argument("--preflight-only", action="store_true")
    p.add_argument("--preflight", type=Path)
    p.add_argument("--base-report", type=Path)
    p.add_argument("--max-tokens", type=int, default=4096)
    p.add_argument("--max-padded-tokens", type=int, default=16384)
    p.add_argument("--max-train-rows", type=int, default=2048)
    p.add_argument("--eval-rows", type=int, default=256)
    p.add_argument("--epochs", type=int, default=1)
    p.add_argument("--seed", type=int, default=191019)
    p.add_argument("--learning-rate", type=float, default=2e-5)
    p.add_argument("--accumulation", type=int, default=8)
    p.add_argument("--memory-fraction", type=float, default=0.24)
    p.add_argument("--checkpoint-every-steps", type=int, default=0)
    p.add_argument("--resume-from", type=Path)
    p.add_argument("--max-wall-seconds", type=int, default=0)
    p.add_argument("--evaluation-reserve-seconds", type=int, default=3600)
    a = p.parse_args()
    if (a.max_tokens < 1 or a.max_padded_tokens < 1 or a.max_train_rows < 0 or a.eval_rows < 0
            or a.epochs < 1 or a.accumulation < 1 or a.learning_rate <= 0 or not 0 < a.memory_fraction < 1
            or a.checkpoint_every_steps < 0 or a.max_wall_seconds < 0 or a.evaluation_reserve_seconds < 0
            or (a.max_wall_seconds and a.max_wall_seconds <= a.evaluation_reserve_seconds)):
        p.error("invalid positive training or budget parameter")
    if not a.preflight_only and (not a.preflight or not a.base_report):
        p.error("training requires --preflight and --base-report")
    output_existed = a.out.exists()
    try:
        result = prepare(a) if a.preflight_only else run(a)
        print(json.dumps(result))
    except Exception as exc:
        # Never overwrite a previous run's output when this invocation refused it.
        if not output_existed and a.out.exists():
            write_json(a.out / "failure.json", {"errorType": type(exc).__name__, "message": str(exc)[:300]})
            write_json(a.out / "progress.json", {"status": "failed", "errorType": type(exc).__name__})
        raise
