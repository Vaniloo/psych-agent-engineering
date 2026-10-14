"""One complete epoch per role; multiclass loss on finite candidate yes/no margins."""

import argparse
import hashlib
import json
import math
import os
import random
import time
from collections import Counter
from pathlib import Path

from psych_agent_runtime.decision_contract import profile_contract
from psych_agent_runtime.qwen_choice_contract import choices, normalize, render, renderer_spec

from .qwen_memory_data import clean, digest
from .train_qwen_memory import write


def softmax(values, temperature=1):
    highest = max(values)
    scores = [math.exp((v - highest) / temperature) for v in values]
    total = sum(scores)
    return [v / total for v in scores]


def label_bucket(task, label):
    # Position IDs are not semantic classes; do not overweight one rare position.
    return "action" if task == "goal_action" and label.startswith("action_") else label


def summary(rows, policies=None):
    result = {}
    for task in sorted({r["task"] for r in rows}):
        subset = [r for r in rows if r["task"] == task]
        accepted = []
        for r in subset:
            if policies:
                policy = policies[task]
                probs = softmax(r["scores"], policy["temperature"])
                margin = sorted(r["scores"], reverse=True)
                stable = len(margin) == 1 or margin[0] - margin[1] >= 2
                if policy["enabled"] and stable and max(probs) >= policy["threshold"]:
                    accepted.append(r)
        correct = lambda r: (
            r["choice"] in (r["label"] if isinstance(r["label"], list) else [r["label"]])
        )
        result[task] = {
            "rows": len(subset),
            "rawCorrect": sum(correct(r) for r in subset),
            "usableCorrect": sum(correct(r) for r in accepted),
            "acceptedWrong": sum(not correct(r) for r in accepted),
            "abstained": len(subset) - len(accepted) if policies else None,
            "confusion": dict(
                Counter(str(r["label"]) + " -> " + r["choice"] for r in subset if not correct(r))
            ),
        }
    return result


def calibrate(profile, calibration, validation):
    _, tasks = profile_contract(profile)
    policies = {}
    for task in tasks:
        cal = [r for r in calibration if r["task"] == task]
        val = [r for r in validation if r["task"] == task]
        if not cal or not val:
            policies[task] = {
                "temperature": 1,
                "threshold": 1,
                "enabled": False,
                "calibrated": False,
                "validationScope": "no_calibration_support",
            }
            continue
        counts = Counter(label_bucket(task, r["label"]) for r in cal)

        def loss(t, cal=cal, counts=counts, task=task):
            total = 0
            for r in cal:
                total -= (
                    math.log(max(softmax(r["scores"], t)[r["labels"].index(r["label"])], 1e-30))
                    / counts[label_bucket(task, r["label"])]
                )
            return total

        temperature = min((0.25, 0.5, 0.75, 1, 1.5, 2, 3, 4, 6, 8), key=loss)
        bucket = lambda label, task=task: (
            "action" if task == "goal_action" and label.startswith("action_") else label
        )
        support = {
            label: len({r["family"] for r in val if bucket(r["label"]) == label})
            for label in {bucket(r["label"]) for r in val}
        }
        threshold = None
        for proposed in (0.85, 0.9, 0.95, 0.975, 0.99):
            accepted = []
            for r in val:
                ordered = sorted(r["scores"], reverse=True)
                stable = len(ordered) == 1 or ordered[0] - ordered[1] >= 2
                if stable and max(softmax(r["scores"], temperature)) >= proposed:
                    accepted.append(r)
            if (
                len({r["family"] for r in accepted}) >= 20
                and min(support.values(), default=0) >= 8
                and sum(r["choice"] != r["label"] for r in accepted) / len(accepted) <= 0.10
            ):
                threshold = proposed
                break
        policies[task] = {
            "temperature": temperature,
            "threshold": threshold or 1,
            "enabled": threshold is not None,
            "calibrated": True,
            "minimumRawMargin": 2,
            "validationLabelGroups": support,
            "validationScope": "programmatic_development_not_independent_human_gold",
        }
    return policies


def predict(model, tokenizer, rows, profile, device, torch, batch=2):
    model.eval()
    result = []
    yes, no = (tokenizer.convert_tokens_to_ids(t) for t in ("yes", "no"))
    with torch.inference_mode():
        for offset in range(0, len(rows), batch):
            chunk = rows[offset : offset + batch]
            groups = []
            texts = []
            for row in chunk:
                labels = list(choices(profile, row["task"], row["state"]))
                groups.append(labels)
                texts.extend(render(profile, row["task"], row["state"], label) for label in labels)
            inputs = tokenizer(texts, padding=True, return_tensors="pt", truncation=False)
            if inputs.input_ids.shape[1] > 2048:
                raise ValueError("evaluation token budget exceeded")
            logits = (
                model(**inputs.to(device), use_cache=False, logits_to_keep=1)
                .logits[:, -1, [no, yes]]
                .float()
            )
            values = (logits[:, 1] - logits[:, 0]).cpu().tolist()
            start = 0
            for row, labels in zip(chunk, groups, strict=True):
                scores = values[start : start + len(labels)]
                start += len(labels)
                if not all(math.isfinite(v) for v in scores):
                    raise ValueError("non-finite logits")
                result.append(
                    {
                        **row,
                        "labels": labels,
                        "scores": scores,
                        "choice": labels[max(range(len(scores)), key=scores.__getitem__)],
                    }
                )
    return result


def run(args):
    if args.out.exists():
        raise ValueError("refuse to overwrite a training run")
    args.out.mkdir(parents=True)
    started = time.monotonic()
    write(args.out / "progress.json", {"status": "loading", "epochs": 1})
    for name, child in [
        ("CUDA_CACHE_PATH", "cuda"),
        ("TORCH_HOME", "torch"),
        ("XDG_CACHE_HOME", "xdg"),
        ("TMPDIR", "tmp"),
    ]:
        path = args.out / "cache" / child
        path.mkdir(parents=True)
        os.environ[name] = str(path)
    import torch
    from peft import LoraConfig, get_peft_model
    from transformers import AutoModelForCausalLM, AutoTokenizer

    seed = 1413 if args.profile == "router" else 1414
    torch.manual_seed(seed)
    random.seed(seed)
    torch.set_num_threads(4)
    torch.cuda.set_per_process_memory_fraction(0.30, args.device)
    manifest = json.loads((args.data / "manifest.json").read_text())
    if manifest["profile"] != args.profile or manifest["rendererSha256"] != digest(
        renderer_spec(args.profile)
    ):
        raise ValueError("course renderer mismatch")
    parts = {}
    for split in ("train", "calibration", "validation", "test"):
        blob = (args.data / (split + ".jsonl")).read_bytes()
        if hashlib.sha256(blob).hexdigest() != manifest["hashes"][split]:
            raise ValueError("course hash mismatch")
        parts[split] = [json.loads(l) for l in blob.decode().splitlines()]
        if not parts[split]:
            raise ValueError("empty course split")
    blob = args.challenge.read_bytes()
    frozen = json.loads(args.challenge.with_name("manifest.json").read_text())
    if hashlib.sha256(blob).hexdigest() != frozen["casesSha256"]:
        raise ValueError("challenge hash mismatch")
    challenge = []
    for row in [json.loads(l) for l in blob.decode().splitlines()]:
        if row["profile"] != args.profile:
            continue
        for task, label in row["expected"].items():
            challenge.append(
                {
                    **row,
                    "task": task,
                    "label": label,
                    "state": clean(normalize(args.profile, task, row["state"])),
                }
            )
    base_report = json.loads(args.base_report.read_text())
    if args.base.name != base_report["revision"]:
        raise ValueError("base revision mismatch")
    for name, expected in base_report["weightsSha256"].items():
        with (args.base / name).open("rb") as stream:
            if hashlib.file_digest(stream, "sha256").hexdigest() != expected:
                raise ValueError("base checksum mismatch")
    tokenizer = AutoTokenizer.from_pretrained(args.base, padding_side="left", local_files_only=True)
    encoded = []
    max_tokens = 0
    for row in parts["train"]:
        labels = list(choices(args.profile, row["task"], row["state"]))
        tokens = [
            tokenizer(render(args.profile, row["task"], row["state"], label)) for label in labels
        ]
        longest = max(len(t["input_ids"]) for t in tokens)
        max_tokens = max(max_tokens, longest)
        if longest > 2048:
            raise ValueError("training token budget exceeded; no silent truncation")
        encoded.append((row, labels, tokens))
    random.shuffle(encoded)
    model = AutoModelForCausalLM.from_pretrained(
        args.base, torch_dtype=torch.bfloat16, attn_implementation="sdpa", local_files_only=True
    ).to(args.device)
    write(args.out / "progress.json", {"status": "base_evaluation", "decisions": len(challenge)})
    before = predict(model, tokenizer, challenge, args.profile, args.device, torch)
    write(args.out / "base-challenge.json", {"summary": summary(before), "cases": before})
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
    counts = Counter(
        (r["task"], label_bucket(r["task"], r["label"])) for r, labels, tokens in encoded
    )
    nclasses = Counter(task for task, label in counts)
    ntasks = len(nclasses)
    weights = {k: len(encoded) / (ntasks * nclasses[k[0]] * v) for k, v in counts.items()}
    yes, no = (tokenizer.convert_tokens_to_ids(t) for t in ("yes", "no"))
    if any(
        tokenizer.encode(t, add_special_tokens=False) != [i] for t, i in (("yes", yes), ("no", no))
    ):
        raise ValueError("non-atomic scoring tokens")
    report = {
        "status": "training",
        "profile": args.profile,
        "epochs": 1,
        "baseModel": base_report["model"],
        "baseRevision": base_report["revision"],
        "baseWeightsSha256": base_report["weightsSha256"],
        "trainableParameters": sum(p.numel() for p in trainable),
        "method": "BF16 LoRA r16 alpha32; grouped multiclass CE on yes/no candidate margins",
        "trainingRows": len(encoded),
        "microBatch": 2,
        "gradientAccumulation": 8,
        "learningRate": 1e-4,
        "seed": seed,
        "maxTrainingTokens": max_tokens,
        "datasetManifest": manifest,
        "challengeSha256": frozen["casesSha256"],
        "rendererSha256": digest(renderer_spec(args.profile)),
        "deploymentEnabled": False,
        "teacherApiUsed": False,
    }
    write(args.out / "training-report.json", report)
    model.train()
    optimizer.zero_grad()
    steps = 0
    total_loss = 0
    training_started = time.monotonic()
    batches = math.ceil(len(encoded) / 2)
    for index, offset in enumerate(range(0, len(encoded), 2)):
        chunk = encoded[offset : offset + 2]
        inputs = tokenizer.pad(
            [t for r, labels, tokens in chunk for t in tokens], padding=True, return_tensors="pt"
        ).to(args.device)
        logits = model(**inputs, use_cache=False, logits_to_keep=1).logits[:, -1, [no, yes]].float()
        scores = logits[:, 1] - logits[:, 0]
        begin = 0
        losses = []
        for row, labels, tokens in chunk:
            end = begin + len(labels)
            target = torch.tensor([labels.index(row["label"])], device=args.device)
            losses.append(
                torch.nn.functional.cross_entropy(scores[begin:end].unsqueeze(0), target)
                * weights[(row["task"], label_bucket(row["task"], row["label"]))]
            )
            begin = end
        loss = torch.stack(losses).mean()
        if not torch.isfinite(loss):
            raise ValueError("non-finite training loss")
        window = min(8, batches - (index // 8) * 8)
        (loss / window).backward()
        total_loss += float(loss.detach())
        if (index + 1) % 8 == 0 or index + 1 == batches:
            torch.nn.utils.clip_grad_norm_(trainable, 1)
            steps += 1
            rate = 1e-4 * min(1, steps / 10) * max(0.1, 1 - steps / math.ceil(batches / 8))
            for group in optimizer.param_groups:
                group["lr"] = rate
            optimizer.step()
            optimizer.zero_grad()
        if (index + 1) % 40 == 0 or index + 1 == batches:
            elapsed = time.monotonic() - training_started
            progress = {
                "status": "training",
                "batchesCompleted": index + 1,
                "totalBatches": batches,
                "optimizerSteps": steps,
                "meanLoss": total_loss / (index + 1),
                "elapsedSeconds": elapsed,
                "estimatedRemainingSeconds": elapsed * (batches - index - 1) / (index + 1),
            }
            write(args.out / "progress.json", progress)
            print(json.dumps(progress), flush=True)
    model.save_pretrained(args.out / "adapter", safe_serialization=True)
    tokenizer.save_pretrained(args.out / "adapter")
    optimization_seconds = time.monotonic() - training_started
    model.gradient_checkpointing_disable()
    write(args.out / "progress.json", {"status": "calibration", "epochsCompleted": 1})
    cal = predict(model, tokenizer, parts["calibration"], args.profile, args.device, torch)
    val = predict(model, tokenizer, parts["validation"], args.profile, args.device, torch)
    policies = calibrate(args.profile, cal, val)
    for name, rows in [
        ("calibration", cal),
        ("validation", val),
        ("test", predict(model, tokenizer, parts["test"], args.profile, args.device, torch)),
        ("challenge", predict(model, tokenizer, challenge, args.profile, args.device, torch)),
    ]:
        write(
            args.out / (name + "-results.json"), {"summary": summary(rows, policies), "cases": rows}
        )
    report.update(
        status="complete",
        optimizerSteps=steps,
        meanTrainingLoss=total_loss / batches,
        optimizationSeconds=optimization_seconds,
        totalSeconds=time.monotonic() - started,
        taskPolicies=policies,
        baseChallenge=summary(before),
        candidateChallenge=json.loads((args.out / "challenge-results.json").read_text())["summary"],
        test=json.loads((args.out / "test-results.json").read_text())["summary"],
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
    parser.add_argument("--profile", choices=["router", "executor"], required=True)
    for name in ("base", "base-report", "data", "challenge", "out"):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--device", default="cuda:0")
    run(parser.parse_args())
