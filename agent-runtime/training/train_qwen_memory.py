"""One-epoch BF16 LoRA Memory adaptation with separate calibration/validation.

Seven binary tasks, shared single-forward renderer. Never alters either public
base or running workers. Challenge labels never select weights or thresholds.
"""

import argparse
import hashlib
import json
import math
import os
import random
import time
from collections import Counter
from pathlib import Path

from psych_agent_runtime.qwen_memory_contract import INSTRUCTIONS, render


def sha(value):
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True).encode()
    ).hexdigest()


def write(path, value):
    temporary = path.with_suffix(".pending.json")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")
    temporary.replace(path)


def predictions(model, tokenizer, rows, device, torch, batch_size=4):
    model.eval()
    result = []
    yes, no = (tokenizer.convert_tokens_to_ids(t) for t in ("yes", "no"))
    with torch.inference_mode():
        for offset in range(0, len(rows), batch_size):
            batch = rows[offset : offset + batch_size]
            inputs = tokenizer(
                [render(r["task"], r["state"]) for r in batch],
                padding=True,
                return_tensors="pt",
                truncation=False,
            )
            if inputs.input_ids.shape[1] > 2048:
                raise ValueError("evaluation exceeds explicit token budget")
            output = model(**inputs.to(device), use_cache=False, logits_to_keep=1)
            values = output.logits[:, -1, [no, yes]].float().cpu().tolist()
            for row, (a, b) in zip(batch, values, strict=True):
                delta = b - a
                result.append(
                    {**row, "logitDifference": delta, "choice": "yes" if delta >= 0 else "no"}
                )
    return result


def aggregate(rows, policies=None):
    result = {}
    for task in INSTRUCTIONS:
        subset = [r for r in rows if r["task"] == task]
        if not subset:
            continue
        policy = (policies or {}).get(task)
        accepted = []
        if policy:
            for r in subset:
                z = abs(r["logitDifference"]) / policy["temperature"]
                p = 1 / (1 + math.exp(-z))
                if policy["enabled"] and p >= policy["threshold"]:
                    accepted.append(r)
        result[task] = {
            "rows": len(subset),
            "rawCorrect": sum(r["choice"] == r["label"] for r in subset),
            "falsePositive": sum(r["choice"] == "yes" and r["label"] == "no" for r in subset),
            "falseNegative": sum(r["choice"] == "no" and r["label"] == "yes" for r in subset),
            "usableCorrect": sum(r["choice"] == r["label"] for r in accepted),
            "acceptedWrong": sum(r["choice"] != r["label"] for r in accepted),
            "abstained": len(subset) - len(accepted) if policy else None,
        }
    return result


def calibrate(calibration, validation):
    policies = {}
    for task in INSTRUCTIONS:
        cal = [r for r in calibration if r["task"] == task]
        val = [r for r in validation if r["task"] == task]
        counts = Counter(r["label"] for r in cal)

        def loss(temperature, rows=cal, label_counts=counts):
            total = 0
            for r in rows:
                z = r["logitDifference"] / temperature
                # Stable binary NLL uses the unrounded logit difference.
                nll = max(z, 0) + math.log1p(math.exp(-abs(z))) - (r["label"] == "yes") * z
                total += nll / label_counts[r["label"]]
            return total

        temperature = min((0.25, 0.5, 0.75, 1, 1.5, 2, 3, 4, 6, 8), key=loss)
        selected = None
        for threshold in (0.85, 0.9, 0.95, 0.975, 0.99):
            accepted = [
                r
                for r in val
                if 1 / (1 + math.exp(-abs(r["logitDifference"]) / temperature)) >= threshold
            ]
            groups = {r["family"] for r in accepted}
            wrong = sum(r["choice"] != r["label"] for r in accepted)
            support = {
                label: len({r["family"] for r in val if r["label"] == label})
                for label in ("yes", "no")
            }
            if len(groups) >= 20 and min(support.values()) >= 8 and wrong / len(accepted) <= 0.10:
                selected = threshold
                break
        policies[task] = {
            "temperature": temperature,
            "threshold": selected or 1.0,
            "enabled": selected is not None,
            "calibrated": True,
            "validationScope": "programmatic_development_not_independent_human_gold",
        }
    return policies


def run(args):
    if args.out.exists():
        raise ValueError("refuse to overwrite a training run")
    args.out.mkdir(parents=True)
    for env, child in [
        ("CUDA_CACHE_PATH", "cuda"),
        ("TORCH_HOME", "torch"),
        ("XDG_CACHE_HOME", "xdg"),
        ("TMPDIR", "tmp"),
    ]:
        p = args.out / "cache" / child
        p.mkdir(parents=True, exist_ok=True)
        os.environ[env] = str(p)
    import torch
    from peft import LoraConfig, get_peft_model
    from transformers import AutoModelForCausalLM, AutoTokenizer

    torch.manual_seed(1412)
    random.seed(1412)
    torch.set_num_threads(4)
    torch.cuda.set_per_process_memory_fraction(0.30, args.device)
    manifest = json.loads((args.data / "manifest.json").read_text())
    if sha(INSTRUCTIONS) != manifest["instructionsSha256"]:
        raise ValueError("course renderer changed")
    parts = {}
    for split in ("train", "calibration", "validation", "test"):
        raw = (args.data / (split + ".jsonl")).read_bytes()
        if hashlib.sha256(raw).hexdigest() != manifest["hashes"][split]:
            raise ValueError("frozen course changed")
        parts[split] = [json.loads(l) for l in raw.decode().splitlines()]
    raw = args.challenge.read_bytes()
    challenge_manifest = json.loads(args.challenge.with_name("manifest.json").read_text())
    if hashlib.sha256(raw).hexdigest() != challenge_manifest["casesSha256"]:
        raise ValueError("frozen challenge changed")
    challenge = []
    for c in [json.loads(l) for l in raw.decode().splitlines()]:
        task, label = next(iter(c["expected"].items()))
        challenge.append({**c, "task": task, "label": label})
    tokenizer = AutoTokenizer.from_pretrained(args.base, padding_side="left", local_files_only=True)
    model = AutoModelForCausalLM.from_pretrained(
        args.base,
        torch_dtype=torch.bfloat16,
        attn_implementation="sdpa",
        local_files_only=True,
    ).to(args.device)
    base_report = json.loads(args.base_report.read_text())
    for name, expected in base_report["weightsSha256"].items():
        with (args.base / name).open("rb") as stream:
            if hashlib.file_digest(stream, "sha256").hexdigest() != expected:
                raise ValueError("base weight hash changed")
    before = predictions(model, tokenizer, challenge, args.device, torch)
    write(args.out / "base-challenge.json", {"summary": aggregate(before), "cases": before})
    model = get_peft_model(
        model,
        LoraConfig(
            r=16,
            lora_alpha=32,
            lora_dropout=0.05,
            bias="none",
            task_type="CAUSAL_LM",
            target_modules=[
                "q_proj",
                "k_proj",
                "v_proj",
                "o_proj",
                "gate_proj",
                "up_proj",
                "down_proj",
            ],
        ),
    )
    model.gradient_checkpointing_enable(gradient_checkpointing_kwargs={"use_reentrant": False})
    model.enable_input_require_grads()
    trainable = [p for p in model.parameters() if p.requires_grad]
    optimizer = torch.optim.AdamW(trainable, lr=1e-4, weight_decay=0.01)
    encoded = []
    for row in parts["train"]:
        tokens = tokenizer(render(row["task"], row["state"]), add_special_tokens=True)
        if len(tokens["input_ids"]) > 1024:
            raise ValueError("training exceeds explicit token budget; no silent truncation")
        encoded.append((row, tokens))
    random.shuffle(encoded)
    counts = Counter((r["task"], r["label"]) for r, _ in encoded)
    weights = {k: len(encoded) / (14 * v) for k, v in counts.items()}
    yes, no = (tokenizer.convert_tokens_to_ids(t) for t in ("yes", "no"))
    if any(
        tokenizer.encode(t, add_special_tokens=False) != [i] for t, i in (("yes", yes), ("no", no))
    ):
        raise ValueError("non-atomic scoring tokens")
    report = {
        "status": "training",
        "epochs": 1,
        "baseModel": base_report["model"],
        "baseRevision": base_report["revision"],
        "baseWeightsSha256": base_report["weightsSha256"],
        "trainableParameters": sum(p.numel() for p in trainable),
        "method": "BF16 LoRA r16 alpha32 all transformer projection layers",
        "trainingRows": len(encoded),
        "learningRate": 1e-4,
        "microBatch": 4,
        "gradientAccumulation": 4,
        "seed": 1412,
        "datasetManifest": manifest,
        "challengeSha256": challenge_manifest["casesSha256"],
        "deploymentEnabled": False,
    }
    write(args.out / "training-report.json", report)
    model.train()
    optimizer.zero_grad()
    steps, total_loss, started = 0, 0, time.monotonic()
    batches = math.ceil(len(encoded) / 4)
    for batch_index, offset in enumerate(range(0, len(encoded), 4)):
        batch = encoded[offset : offset + 4]
        inputs = tokenizer.pad([x for _, x in batch], padding=True, return_tensors="pt").to(
            args.device
        )
        labels = torch.tensor(
            [r["label"] == "yes" for r, _ in batch], device=args.device, dtype=torch.long
        )
        sample_weights = torch.tensor(
            [weights[(r["task"], r["label"])] for r, _ in batch], device=args.device
        )
        logits = model(**inputs, use_cache=False, logits_to_keep=1).logits[:, -1, [no, yes]].float()
        loss = (
            torch.nn.functional.cross_entropy(logits, labels, reduction="none") * sample_weights
        ).mean()
        if not torch.isfinite(loss):
            raise ValueError("non-finite training loss")
        # The final accumulation window may have fewer than four batches.
        window = min(4, batches - (batch_index // 4) * 4)
        (loss / window).backward()
        total_loss += float(loss.detach())
        if (batch_index + 1) % 4 == 0 or batch_index + 1 == batches:
            torch.nn.utils.clip_grad_norm_(trainable, 1.0)
            steps += 1
            warmup = 10
            rate = 1e-4 * min(1, steps / warmup) * max(0.1, 1 - steps / math.ceil(batches / 4))
            for group in optimizer.param_groups:
                group["lr"] = rate
            optimizer.step()
            optimizer.zero_grad()
        if (batch_index + 1) % 40 == 0 or batch_index + 1 == batches:
            progress = {
                "status": "training",
                "batchesCompleted": batch_index + 1,
                "totalBatches": batches,
                "optimizerSteps": steps,
                "meanLoss": total_loss / (batch_index + 1),
                "elapsedSeconds": time.monotonic() - started,
            }
            write(args.out / "progress.json", progress)
            print(json.dumps(progress), flush=True)
    model.save_pretrained(args.out / "adapter", safe_serialization=True)
    tokenizer.save_pretrained(args.out / "adapter")
    write(args.out / "progress.json", {"status": "evaluating", "epochsCompleted": 1})
    model.gradient_checkpointing_disable()
    cal = predictions(model, tokenizer, parts["calibration"], args.device, torch)
    val = predictions(model, tokenizer, parts["validation"], args.device, torch)
    policies = calibrate(cal, val)
    test = predictions(model, tokenizer, parts["test"], args.device, torch)
    after = predictions(model, tokenizer, challenge, args.device, torch)
    for name, rows in [
        ("calibration", cal),
        ("validation", val),
        ("test", test),
        ("challenge", after),
    ]:
        write(
            args.out / (name + "-results.json"),
            {"summary": aggregate(rows, policies), "cases": rows},
        )
    report.update(
        status="complete",
        optimizerSteps=steps,
        meanTrainingLoss=total_loss / batches,
        trainingSeconds=time.monotonic() - started,
        taskPolicies=policies,
        baseChallenge=aggregate(before),
        candidateChallenge=aggregate(after, policies),
        test=aggregate(test, policies),
        peakAllocatedMiB=torch.cuda.max_memory_allocated() / 1024**2,
    )
    report["adapterSha256"] = hashlib.sha256(
        (args.out / "adapter/adapter_model.safetensors").read_bytes()
    ).hexdigest()
    write(args.out / "training-report.json", report)
    write(args.out / "progress.json", {"status": "complete", "epochsCompleted": 1})
    print(json.dumps({"status": "complete", "challenge": report["candidateChallenge"]}), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("base", "base-report", "data", "challenge", "out"):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--device", default="cuda:0")
    run(parser.parse_args())
