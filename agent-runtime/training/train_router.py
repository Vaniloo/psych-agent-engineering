"""Train locally; select on validation, calibrate separately, test only once.

All writable paths (including package/model caches) are set by the lab launcher.
Bootstrap results establish engineering feasibility, not clinical validity.
"""

from __future__ import annotations

import argparse
import gc
import hashlib
import json
import math
import os
import time
from pathlib import Path

import numpy as np
import torch

from psych_agent_runtime.routing_contract import CONTRACT_VERSION, QUESTIONS, ROUTES


def read_rows(path: Path) -> list[dict]:
    return [
        json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()
    ]


def distribution_metrics(rows: list[dict], probabilities: np.ndarray) -> dict:
    labels = list(ROUTES)
    y = np.asarray([labels.index(item["expected"]["route"]) for item in rows])
    pred = probabilities.argmax(axis=1)
    confusion = np.zeros((len(labels), len(labels)), dtype=int)
    for actual, guessed in zip(y, pred, strict=True):
        confusion[actual, guessed] += 1
    f1 = []
    for index in range(len(labels)):
        tp = confusion[index, index]
        f1.append(float(2 * tp / max(1, confusion[index].sum() + confusion[:, index].sum())))
    confidence = probabilities.max(axis=1)
    correct = pred == y
    ece = 0.0
    for lower in np.arange(0, 1, 0.1):
        mask = (confidence > lower) & (confidence <= lower + 0.1 + 1e-8)
        if mask.any():
            ece += float(mask.mean() * abs(confidence[mask].mean() - correct[mask].mean()))
    onehot = np.eye(len(labels))[y]
    return {
        "items": len(rows),
        "accuracy": float(correct.mean()),
        "macroF1": float(np.mean(f1)),
        "ece": ece,
        "brier": float(np.square(probabilities - onehot).sum(axis=1).mean()),
        "perRouteF1": dict(zip(labels, f1, strict=True)),
        "confusion": confusion.tolist(),
    }


def infer(agent, rows: list[dict]) -> tuple[np.ndarray, dict]:
    started = time.perf_counter()
    results = agent.predict_batch([row["state"] for row in rows], QUESTIONS, batch_size=8)
    probabilities = np.asarray(
        [
            [result["answers"]["route"]["probabilities"][name] for name in ROUTES]
            for result in results
        ],
        dtype=float,
    )
    return probabilities, {"batchAverageMs": (time.perf_counter() - started) * 1000 / len(rows)}


def scale(probabilities: np.ndarray, temperature: float) -> np.ndarray:
    logits = np.log(np.clip(probabilities, 1e-9, 1)) / temperature
    logits -= logits.max(axis=1, keepdims=True)
    exp = np.exp(logits)
    return exp / exp.sum(axis=1, keepdims=True)


def fit_temperature(rows: list[dict], probabilities: np.ndarray) -> float:
    y = np.asarray([list(ROUTES).index(item["expected"]["route"]) for item in rows])

    def loss(t):
        return -np.log(np.clip(scale(probabilities, t)[np.arange(len(y)), y], 1e-9, 1)).mean()

    return float(min(np.linspace(0.5, 5, 181), key=loss))


def fit_gate(rows: list[dict], probabilities: np.ndarray) -> dict:
    """Choose maximum validation coverage with a Wilson 95% upper error <=15%.

    This is an empirical bootstrap gate; scenario-correlated synthetic rows do
    not establish real-world risk. Use one canonical row per scenario for gating.
    """
    selected = {}
    for index, row in enumerate(rows):
        selected.setdefault(row["scenario_id"], index)
    indices = list(selected.values())
    y = np.asarray([list(ROUTES).index(rows[i]["expected"]["route"]) for i in indices])
    probs = probabilities[indices]
    pred, confidence = probs.argmax(axis=1), probs.max(axis=1)
    best = {"enabled": False, "threshold": 1.0, "coverage": 0.0, "acceptedScenarios": 0}
    for threshold in np.linspace(0.5, 0.99, 50):
        mask = confidence >= threshold
        n = int(mask.sum())
        if n < 20:
            continue
        error = float((pred[mask] != y[mask]).mean())
        z = 1.645  # one-sided 95% Wilson interval
        upper = (
            error + z * z / (2 * n) + z * math.sqrt(error * (1 - error) / n + z * z / (4 * n * n))
        ) / (1 + z * z / n)
        if upper <= 0.15 and mask.mean() > best["coverage"]:
            best = {
                "enabled": True,
                "threshold": float(threshold),
                "coverage": float(mask.mean()),
                "acceptedScenarios": n,
                "observedError": error,
                "wilsonUpperError": upper,
            }
    return best


def main(args) -> None:
    import laya
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
        torch.cuda.set_per_process_memory_fraction(0.22)
    parts = {
        name: read_rows(args.data / f"{name}.jsonl")
        for name in ("train", "calibration", "validation", "test")
    }
    seen = set()
    for name, rows in parts.items():
        ids = {row["scenario_id"] for row in rows}
        if seen & ids:
            raise ValueError(f"scenario leakage in {name}")
        seen |= ids
    model_dir = resolve_checkpoint_dir(args.base)
    base_agent = laya.load(model_dir, device=str(device))
    baseline_probs, _ = infer(base_agent, parts["test"])
    baseline = distribution_metrics(parts["test"], baseline_probs)
    print(json.dumps({"baseline": baseline}), flush=True)
    del base_agent
    gc.collect()
    if device.type == "cuda":
        torch.cuda.empty_cache()
    model, tokenizer, cfg = load_checkpoint(model_dir)
    # ModernBERT otherwise invokes an internal torch.compile path in training,
    # which requires a system C compiler even when Laya compile=False.
    if hasattr(model.encoder.config, "reference_compile"):
        model.encoder.config.reference_compile = False
    model.to(device)
    max_len, head_len = 512, 256
    train_items, skipped = items_from_rows(tokenizer, parts["train"], max_len, head_len)
    val_items, val_skipped = items_from_rows(tokenizer, parts["validation"], max_len, head_len)
    if skipped or val_skipped or len(train_items) != len(parts["train"]):
        raise ValueError(f"invalid training rows: {skipped}, validation: {val_skipped}")
    args.out.mkdir(parents=True, exist_ok=True)
    cfg.update(
        max_len=max_len, head_max_len=head_len, amp_dtype="fp16", temperature=[1.0, 1.0, 1.0]
    )
    cfg.pop("temperature_by_options", None)
    best_loss = float("inf")
    epoch_metrics = []

    def checkpoint(epoch, train_loss):
        nonlocal best_loss
        records = calibration_records(model, tokenizer, val_items, device, max_len, head_len)
        metrics = evaluate_records(records)
        epoch_metrics.append({"epoch": epoch, "trainLoss": train_loss, "validation": metrics})
        print(json.dumps(epoch_metrics[-1]), flush=True)
        if metrics["loss"] < best_loss:
            best_loss = metrics["loss"]
            save_checkpoint(model, tokenizer, cfg, str(args.out))
        model.train()

    config = TrainConfig(
        epochs=args.epochs,
        micro_batch=8,
        grad_accum=4,
        loss=args.loss,
        shuffle_options=("choice",),
        seed=args.seed,
        max_len=max_len,
        head_max_len=head_len,
        gradient_checkpointing=False,
    )
    started = time.perf_counter()
    train_model(model, tokenizer, train_items, config, device, max_len, head_len, checkpoint)
    model = None
    gc.collect()
    if device.type == "cuda":
        torch.cuda.empty_cache()
    agent = laya.load(str(args.out), device=str(device))
    cal_probs, _ = infer(agent, parts["calibration"])
    temperature = fit_temperature(parts["calibration"], cal_probs)
    config_path = args.out / "rl_agent_config.json"
    served_cfg = json.loads(config_path.read_text())
    served_cfg["temperature"] = [temperature, 1.0, 1.0]
    served_cfg.pop("temperature_by_options", None)
    config_path.write_text(json.dumps(served_cfg, indent=2), encoding="utf-8")
    # Reload the saved checkpoint: evaluate precisely the artifact being served.
    del agent
    gc.collect()
    if device.type == "cuda":
        torch.cuda.empty_cache()
    agent = laya.load(str(args.out), device=str(device))
    val_probs, _ = infer(agent, parts["validation"])
    gate = fit_gate(parts["validation"], val_probs)
    test_probs, speed = infer(agent, parts["test"])
    errors = [
        {
            "scenario": row["scenario_id"],
            "state": row["state"],
            "expected": row["expected"]["route"],
            "actual": list(ROUTES)[int(p.argmax())],
        }
        for row, p in zip(parts["test"], test_probs, strict=True)
        if list(ROUTES)[int(p.argmax())] != row["expected"]["route"]
    ]
    with (args.out / "model.safetensors").open("rb") as stream:
        weights_sha256 = hashlib.file_digest(stream, "sha256").hexdigest()
    summary = {
        "contractVersion": CONTRACT_VERSION,
        "base": args.base,
        "loss": args.loss,
        "scope": "synthetic_bootstrap",
        "seed": args.seed,
        "rows": {name: len(rows) for name, rows in parts.items()},
        "temperature": temperature,
        "epochs": epoch_metrics,
        "validation": distribution_metrics(parts["validation"], val_probs),
        "baseline": baseline,
        "test": distribution_metrics(parts["test"], test_probs),
        "gate": gate,
        "inference": speed,
        "durationSeconds": time.perf_counter() - started,
        "errors": errors,
        "weightsSha256": weights_sha256,
    }
    # Promotion remains explicit: deployment metadata records whether the gate passed.
    deployment = {
        "contractVersion": CONTRACT_VERSION,
        "model": f"psych-route-{args.loss}-s{args.seed}",
        "calibrated": True,
        "enabled": gate["enabled"],
        "threshold": gate["threshold"],
        "validationScope": "synthetic_bootstrap",
        "amp": "fp16",
        "weightsSha256": summary["weightsSha256"],
    }
    for name, data in (("training-report.json", summary), ("deployment.json", deployment)):
        (args.out / name).write_text(
            json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    print(
        json.dumps(
            {key: summary[key] for key in ("rows", "test", "gate", "durationSeconds")},
            ensure_ascii=False,
            indent=2,
        ),
        flush=True,
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--data", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--base", default="convaiinnovations/laya-multilingual")
    parser.add_argument("--loss", choices=["soft-ce", "rlcd"], default="soft-ce")
    parser.add_argument("--epochs", type=int, default=4)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--device", default="cuda")
    main(parser.parse_args())
