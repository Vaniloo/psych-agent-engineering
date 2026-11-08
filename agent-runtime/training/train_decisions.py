"""Fine-tune one of three independent Laya profiles; calibrate each task separately.

Test data is evaluated once after checkpoint selection. Teacher-only runs are
engineering experiments and never produce enabled deployment policies.
"""

import argparse
import gc
import hashlib
import json
import math
from collections import Counter, defaultdict
from pathlib import Path

from psych_agent_runtime.decision_contract import PROFILES, profile_contract
from psych_agent_runtime.routing_service import questions_digest

from .decision_data import load_parts


def scaled(probs, temperature):
    logits = {k: math.log(max(v, 1e-30)) / temperature for k, v in probs.items()}
    peak = max(logits.values())
    scores = {k: math.exp(v - peak) for k, v in logits.items()}
    total = sum(scores.values())
    return {k: v / total for k, v in scores.items()}


def fit_policy(calibration, validation, experimental):
    if len(calibration) < 20 or len(validation) < 20:
        return {
            "enabled": False,
            "calibrated": False,
            "temperature": 1.0,
            "threshold": 1.0,
            "validationScope": "insufficient_holdout_records",
        }
    candidates = [0.5 + i * 0.05 for i in range(91)]
    temperature = min(
        candidates,
        key=lambda t: sum(-math.log(max(scaled(p, t)[label], 1e-30)) for p, label in calibration),
    )
    best = {
        "enabled": False,
        "calibrated": True,
        "temperature": temperature,
        "threshold": 1.0,
        "validationScope": (
            "teacher_only_experiment"
            if experimental
            else "project_policy_holdout_not_independent_review"
        ),
    }
    for threshold in [0.50 + i * 0.01 for i in range(50)]:
        accepted = []
        for probs, label in validation:
            probs = scaled(probs, temperature)
            prediction = max(probs, key=probs.get)
            if probs[prediction] >= threshold:
                accepted.append(prediction != label)
        n = len(accepted)
        if n < 20:
            continue
        error = sum(accepted) / n
        z = 1.645
        upper = (
            error + z * z / (2 * n) + z * math.sqrt(error * (1 - error) / n + z * z / (4 * n * n))
        ) / (1 + z * z / n)
        if upper <= 0.10:
            best.update(
                enabled=not experimental, threshold=threshold, accepted=n, wilsonUpperError=upper
            )
            break
    return best


def label_provenance(parts):
    """Report declared label origins without equating reviewed=True to human gold."""
    return {
        "independentReviewVerified": False,
        "scope": "Declared record metadata only; groups and records are not proved independent.",
        "splits": {
            split: {
                "records": len(rows),
                "splitGroups": len({r["splitGroup"] for r in rows}),
                "annotationStatus": dict(
                    Counter(str(r.get("annotationStatus", "unspecified")) for r in rows)
                ),
                "reviewedBy": dict(
                    Counter(str(r.get("reviewedBy") or "unspecified") for r in rows)
                ),
                "validationScope": dict(
                    Counter(str(r.get("validationScope", "unspecified")) for r in rows)
                ),
            }
            for split, rows in parts.items()
        },
    }


def infer(agent, rows, unrounded=False):
    by_task = defaultdict(list)
    for row in rows:
        answers = agent.predict(row["state"], row["questions"])["answers"]
        for task, label in row["expected"].items():
            probs = answers[task]["unroundedProbabilities" if unrounded else "probabilities"]
            if set(probs) != set(row["questions"][task]["criteria"]):
                raise ValueError("inference option contract mismatch")
            by_task[task].append((probs, label))
    return by_task


def task_metrics(records, policies):
    result = {}
    for task, examples in records.items():
        temperature = policies.get(task, {}).get("temperature", 1)
        confusion = defaultdict(lambda: defaultdict(int))
        errors = 0
        loss = 0
        for probs, label in examples:
            p = scaled(probs, temperature)
            predicted = max(p, key=p.get)
            errors += predicted != label
            confusion[label][predicted] += 1
            loss += -math.log(max(p[label], 1e-30))
        result[task] = {
            "items": len(examples),
            "accuracy": 1 - errors / len(examples),
            "nll": loss / len(examples),
            "confusion": dict(confusion),
        }
    return result


def main(args):
    if any(
        not math.isfinite(value) or value <= 0
        for value in (args.encoder_lr, args.head_lr)
    ):
        raise ValueError("learning rates must be finite and positive")
    contract, questions = profile_contract(args.profile, args.contract)
    parts = load_parts(args.data, args.profile, args.allow_teacher_only, contract)
    target_tasks = args.tasks
    if target_tasks:
        if len(set(target_tasks)) != len(target_tasks) or any(
            t not in questions for t in target_tasks
        ):
            raise ValueError("invalid target tasks")
        parts = {
            split: [
                {
                    **row,
                    "expected": {t: v for t, v in row["expected"].items() if t in target_tasks},
                    "questions": {t: q for t, q in row["questions"].items() if t in target_tasks},
                }
                for row in rows
                if set(row["expected"]) & set(target_tasks)
            ]
            for split, rows in parts.items()
        }
        if any(not rows for rows in parts.values()):
            raise ValueError("target task has empty split")
    experimental = any(
        r.get("reviewed") is not True
        for split, rows in parts.items()
        if split != "train"
        for r in rows
    )
    if args.inspect:
        print(
            json.dumps(
                {
                    "profile": args.profile,
                    "experimental": experimental,
                    "rows": {k: len(v) for k, v in parts.items()},
                },
                indent=2,
            )
        )
        return
    import os

    import laya
    import torch
    from laya.train import (
        TrainConfig,
        calibration_records,
        evaluate_records,
        items_from_rows,
        load_checkpoint,
        resolve_checkpoint_dir,
        save_checkpoint,
        train_model,
    )

    os.environ["LAYA_CUDA_AMP"] = "fp16"
    torch.manual_seed(args.seed)
    torch.set_num_threads(8)
    device = torch.device(args.device)
    if device.type == "cuda":
        torch.cuda.set_per_process_memory_fraction(0.20)
    model, tokenizer, cfg = load_checkpoint(resolve_checkpoint_dir(args.base))
    if hasattr(model.encoder.config, "reference_compile"):
        model.encoder.config.reference_compile = False
    model.to(device)
    max_len, head_len = args.max_len, args.head_max_len
    converted = {}
    for split in ("train", "validation"):
        items, skipped = items_from_rows(tokenizer, parts[split], max_len, head_len)
        if skipped or not items:
            raise ValueError(f"{split} invalid tokenized examples: {skipped}")
        converted[split] = items
    # Serving rejects overlong states. Training must use the same effective budget.
    room = max_len - head_len - 16
    for rows in parts.values():
        for row in rows:
            text = json.dumps(row["state"], ensure_ascii=False).replace(tokenizer.mask_token, " ")
            if len(tokenizer.encode(text, add_special_tokens=False)) > room:
                raise ValueError(
                    "state exceeds serving budget; shorten preprocessing before training"
                )
    cfg.update(
        max_len=max_len, head_max_len=head_len, amp_dtype="fp16", temperature=[1.0, 1.0, 1.0]
    )
    cfg.pop("temperature_by_options", None)
    args.out.mkdir(parents=True, exist_ok=True)
    best, epochs = float("inf"), []

    def checkpoint(epoch, train_loss):
        nonlocal best
        metrics = evaluate_records(
            calibration_records(
                model, tokenizer, converted["validation"], device, max_len, head_len
            )
        )
        epochs.append({"epoch": epoch, "trainLoss": train_loss, "validation": metrics})
        print(json.dumps(epochs[-1]), flush=True)
        if metrics["loss"] < best:
            best = metrics["loss"]
            save_checkpoint(model, tokenizer, cfg, str(args.out))
        model.train()

    config = TrainConfig(
        epochs=args.epochs,
        micro_batch=args.micro_batch,
        grad_accum=4,
        loss="soft-ce",
        shuffle_options=("choice",),
        seed=args.seed,
        max_len=max_len,
        head_max_len=head_len,
        freeze_encoder=args.freeze_encoder,
        encoder_lr=args.encoder_lr,
        head_lr=args.head_lr,
    )
    train_model(model, tokenizer, converted["train"], config, device, max_len, head_len, checkpoint)
    model = None
    gc.collect()
    if device.type == "cuda":
        torch.cuda.empty_cache()
    agent = laya.load(str(args.out), device=str(device))
    unrounded = args.unrounded_calibration
    if unrounded:
        from .unrounded_laya import enable_unrounded_decoding

        enable_unrounded_decoding(agent)
    cal = infer(agent, parts["calibration"], unrounded)
    val = infer(agent, parts["validation"], unrounded)
    policies = {
        task: fit_policy(cal.get(task, []), val.get(task, []), experimental) for task in questions
    }
    test = infer(agent, parts["test"], unrounded)
    if unrounded:
        # Serving still uses upstream rounded probabilities. Do not accidentally
        # deploy temperatures fitted on a different numerical contract.
        for policy in policies.values():
            policy.update(
                enabled=False, servingBlock="offline_unrounded_calibration_not_serving_compatible"
            )
    with (args.out / "model.safetensors").open("rb") as stream:
        checksum = hashlib.file_digest(stream, "sha256").hexdigest()
    metadata = {
        "profile": args.profile,
        "contractVersion": contract,
        "questionsSha256": questions_digest(questions),
        "weightsSha256": checksum,
        "model": f"psych-{args.profile}-s{args.seed}",
        "amp": "fp16",
        "maxStateTokens": room,
        "tasks": policies,
        "labelProvenance": label_provenance(parts),
        "calibrationProbabilityEncoding": (
            "laya_logit_softmax_float64_offline_v1" if unrounded else "laya_probability_rounded4_v1"
        ),
    }
    report = {
        "profile": args.profile,
        "contractVersion": contract,
        "experimental": experimental,
        "rows": {k: len(v) for k, v in parts.items()},
        "epochs": epochs,
        "validation": task_metrics(val, policies),
        "test": task_metrics(test, policies),
        "deploymentEnabled": any(p["enabled"] for p in policies.values()),
        "labelProvenance": metadata["labelProvenance"],
        "calibrationProbabilityEncoding": metadata["calibrationProbabilityEncoding"],
        "trainingConfig": {
            "seed": args.seed,
            "freezeEncoder": config.freeze_encoder,
            "encoderLr": config.encoder_lr,
            "headLr": config.head_lr,
            "microBatch": config.micro_batch,
            "gradAccum": config.grad_accum,
            "targetTasks": target_tasks or list(questions),
        },
    }
    (args.out / "deployment.json").write_text(json.dumps(metadata, indent=2))
    (args.out / "training-report.json").write_text(json.dumps(report, indent=2))
    print(json.dumps(report, indent=2), flush=True)


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--profile", choices=PROFILES, required=True)
    p.add_argument("--contract", help="Explicit versioned contract; defaults to deployed v2")
    p.add_argument(
        "--tasks", nargs="+", help="Train only these tasks; other policies remain disabled"
    )
    p.add_argument(
        "--freeze-encoder", action="store_true", help="Train a task head on the frozen backbone"
    )
    p.add_argument("--data", type=Path, required=True)
    p.add_argument("--out", type=Path, required=True)
    p.add_argument("--base", default="convaiinnovations/laya-multilingual")
    p.add_argument("--device", default="cuda")
    p.add_argument("--epochs", type=int, default=3)
    p.add_argument("--micro-batch", type=int, default=2)
    p.add_argument("--max-len", type=int, default=1536)
    p.add_argument("--head-max-len", type=int, default=512)
    p.add_argument("--seed", type=int, default=42)
    p.add_argument("--encoder-lr", type=float, default=2.5e-5)
    p.add_argument("--head-lr", type=float, default=1e-4)
    p.add_argument("--inspect", action="store_true")
    p.add_argument("--allow-teacher-only", action="store_true")
    p.add_argument(
        "--unrounded-calibration",
        action="store_true",
        help="Offline precision calibration; deployment tasks remain disabled until reviewed serving compatibility",
    )
    main(p.parse_args())
