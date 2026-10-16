"""Single-epoch LoRA binary assertion pilot, separate from all online Memory weights."""

import argparse
import hashlib
import json
import math
import random
import time
from pathlib import Path


def run(args):
    import torch
    import torch.nn.functional as F
    from peft import LoraConfig, get_peft_model
    from transformers import AutoModelForCausalLM, AutoTokenizer

    if args.out.exists():
        raise ValueError("refuse to overwrite trained pilot")
    args.out.mkdir(parents=True)
    random.seed(42)
    torch.manual_seed(42)
    torch.set_num_threads(2)
    torch.cuda.set_per_process_memory_fraction(0.10)
    prompt = json.loads((args.data / "prompt.json").read_text())["system"]
    data = {split: [json.loads(line) for line in (args.data / f"{split}.jsonl").read_text().splitlines()]
            for split in ("train", "calibration", "validation", "test")}
    groups = {split: {r["family"] for r in rows} for split, rows in data.items()}
    if any(groups[a] & groups[b] for a in groups for b in groups if a != b):
        raise ValueError("fact families overlap")
    diagnostic = json.loads(args.diagnostic.read_text())["rows"]
    data["natural_development"] = [r for r in diagnostic if r["task"] == "assertion"
                                   and r["id"] != "assertion-uncertain_possibility"]
    if args.holdout:
        data["fresh_authored"] = json.loads(args.holdout.read_text())["rows"]
    tokenizer = AutoTokenizer.from_pretrained(args.model, local_files_only=True, padding_side="left")
    tokenizer.pad_token = tokenizer.eos_token
    encoded = {}
    for split, rows in data.items():
        encoded[split] = []
        for row in rows:
            text = tokenizer.apply_chat_template([
                {"role": "system", "content": prompt},
                {"role": "user", "content": json.dumps({"source": row["source"]}, ensure_ascii=False)},
            ], tokenize=False, add_generation_prompt=True, enable_thinking=False)
            tokens = tokenizer(text, add_special_tokens=False)["input_ids"]
            if len(tokens) > 512 or row["expected"] not in {"yes", "no"}:
                raise ValueError("invalid pilot row or token budget")
            encoded[split].append((tokens, int(row["expected"] == "yes"), row))
    label_ids = [tokenizer.encode(s, add_special_tokens=False) for s in ("no", "yes")]
    if any(len(s) != 1 for s in label_ids):
        raise ValueError("single-token labels required")
    label_ids = [s[0] for s in label_ids]
    model = AutoModelForCausalLM.from_pretrained(
        args.model, torch_dtype=torch.bfloat16, attn_implementation="sdpa", local_files_only=True,
    ).to("cuda")
    model.config.use_cache = False

    def batch(items):
        inputs = tokenizer.pad({"input_ids": [r[0] for r in items]}, return_tensors="pt").to("cuda")
        labels = torch.tensor([r[1] for r in items], device="cuda")
        return inputs, labels

    def evaluate(split):
        model.eval()
        result = []
        for start in range(0, len(encoded[split]), 8):
            items = encoded[split][start:start + 8]
            inputs, _ = batch(items)
            with torch.inference_mode():
                scores = model(**inputs, logits_to_keep=1).logits[:, -1, label_ids].float()
            margins = (scores[:, 1] - scores[:, 0]).cpu().tolist()
            result.extend({"id": item[2]["id"], "source": item[2]["source"],
                           "expected": item[2]["expected"], "margin": margin}
                          for item, margin in zip(items, margins, strict=True))
        return result

    started = time.perf_counter()
    baseline = {split: evaluate(split) for split in data if split != "train"}
    (args.out / "baseline.json").write_text(json.dumps(baseline, ensure_ascii=False, indent=2) + "\n")
    model = get_peft_model(model, LoraConfig(r=8, lora_alpha=16, lora_dropout=0.05,
        target_modules=["q_proj", "k_proj", "v_proj", "o_proj", "gate_proj", "up_proj", "down_proj"],
        task_type="CAUSAL_LM"))
    train = list(encoded["train"])
    random.Random(42).shuffle(train)
    steps = math.ceil(math.ceil(len(train) / 8) / 2)
    optimizer = torch.optim.AdamW((p for p in model.parameters() if p.requires_grad), lr=1e-4,
                                  weight_decay=0.01)
    optimizer.zero_grad()
    train_started = time.perf_counter()
    losses, step = [], 0
    for micro, start in enumerate(range(0, len(train), 8)):
        model.train()
        inputs, labels = batch(train[start:start + 8])
        scores = model(**inputs, logits_to_keep=1).logits[:, -1, label_ids].float()
        loss = F.cross_entropy(scores, labels, label_smoothing=0.02)
        losses.append(float(loss.detach()))
        (loss / 2).backward()
        if (micro + 1) % 2 == 0 or start + 8 >= len(train):
            step += 1
            warmup = max(1, steps // 10)
            scale = step / warmup if step <= warmup else 0.5 * (1 + math.cos(
                math.pi * (step - warmup) / max(1, steps - warmup)))
            for group in optimizer.param_groups:
                group["lr"] = 1e-4 * scale
            torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
            optimizer.step()
            optimizer.zero_grad()
            if step % 10 == 0 or step == steps:
                print(f"step {step}/{steps} loss {sum(losses[-20:])/len(losses[-20:]):.4f}", flush=True)
    train_seconds = time.perf_counter() - train_started
    model.save_pretrained(args.out / "adapter", safe_serialization=True)
    (args.out / "prompt.json").write_text((args.data / "prompt.json").read_text())
    scored = {split: evaluate(split) for split in data if split != "train"}
    calibration = scored["calibration"]
    def nll(temperature):
        values = torch.tensor([r["margin"] for r in calibration]) / temperature
        labels = torch.tensor([r["expected"] == "yes" for r in calibration], dtype=torch.float32)
        return float(F.binary_cross_entropy_with_logits(values, labels))
    temperature = min([.5, .75, 1., 1.5, 2., 3., 5.], key=nll)
    def probability(margin):
        return float(torch.sigmoid(torch.tensor(margin / temperature)))
    negative_max = max(probability(r["margin"]) for r in calibration if r["expected"] == "no")
    threshold = min(1., max(.80, negative_max + 1e-5))
    def metrics(rows, trained):
        return {"cases": len(rows), "rawCorrect": sum((r["margin"] > 0) == (r["expected"] == "yes") for r in rows),
                "rawFalseAccept": sum(r["margin"] > 0 and r["expected"] == "no" for r in rows),
                "rawFalseReject": sum(r["margin"] <= 0 and r["expected"] == "yes" for r in rows),
                "gateAllowed": sum(probability(r["margin"]) >= threshold for r in rows) if trained else None,
                "gateFalseAccept": sum(probability(r["margin"]) >= threshold and r["expected"] == "no" for r in rows) if trained else None,
                "gateTrueAccept": sum(probability(r["margin"]) >= threshold and r["expected"] == "yes" for r in rows) if trained else None}
    report = {
        "scope": "One-epoch synthetic LoRA pilot, no teacher API. Family-disjoint synthetic partitions share templates. Natural authored probes are not human gold or production proof.",
        "base": str(args.model), "rows": {k: len(v) for k, v in data.items()},
        "steps": step, "epochs": 1, "loraRank": 8, "loraAlpha": 16, "learningRate": 1e-4,
        "trainSeconds": round(train_seconds, 2), "totalSeconds": round(time.perf_counter() - started, 2),
        "peakAllocatedMiB": round(torch.cuda.max_memory_allocated() / 2**20, 1),
        "calibration": {"temperature": temperature, "allowThreshold": threshold,
                        "criterion": "calibration only; no negative acceptance in this calibration sample; no population guarantee"},
        "baseline": {k: metrics(v, False) for k, v in baseline.items()},
        "finetuned": {k: metrics(v, True) for k, v in scored.items()},
        "adapterSha256": hashlib.sha256((args.out / "adapter/adapter_model.safetensors").read_bytes()).hexdigest(),
    }
    (args.out / "scored.json").write_text(json.dumps(scored, ensure_ascii=False, indent=2) + "\n")
    (args.out / "training-report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps(report, ensure_ascii=False, indent=2), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", type=Path, required=True)
    parser.add_argument("--data", type=Path, required=True)
    parser.add_argument("--diagnostic", type=Path, required=True)
    parser.add_argument("--holdout", type=Path)
    parser.add_argument("--out", type=Path, required=True)
    run(parser.parse_args())
