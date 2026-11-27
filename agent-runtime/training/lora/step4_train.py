"""第 4 步：用 LoRA 微调 Qwen3-Reranker-4B，并在测试集上对比微调前后。

打分方式（和 Qwen3-Reranker 原本的用法一样）：
    把“对话 + 所有可用候选”作为 Query、某一个候选作为 Document，渲染成一段提示，
    让模型回答 yes / no。分数 = logit("yes") - logit("no")，只看最后一个位置。
    一条样本有 N 个候选，就渲染 N 段提示、得到 N 个分数。

训练目标：N 个分数一起做 softmax，最大化示范动作的概率（见 common.listwise_loss）。

    python -m training.lora.step4_train --data work/samples \\
        --base <Qwen3-Reranker-4B 本地快照目录> --out work/run
"""

import argparse
import hashlib
import json
import math
import random
import time
from pathlib import Path

from .common import CONTRACT, SPLITS, listwise_loss, metrics, read_jsonl, render, specification


def token_lengths(tokenizer, row) -> list[int]:
    texts = [render(row, i) for i in range(len(row["candidateTools"]))]
    return [len(ids) for ids in tokenizer(texts, add_special_tokens=False)["input_ids"]]


def fits_budget(tokenizer, row, max_tokens: int, max_padded: int) -> bool:
    """不截断任何输入：超长的样本直接排除。截断会切掉工具定义或历史，标签就不再可信。"""
    lengths = token_lengths(tokenizer, row)
    return max(lengths) <= max_tokens and max(lengths) * len(lengths) <= max_padded


def lr_at(step: int, total: int, peak: float, warmup: int = 10) -> float:
    """前 warmup 步线性升到峰值，之后线性衰减，最低保留峰值的 10%。"""
    return peak * min(1.0, step / warmup) * max(0.1, 1 - step / total)


def sha256(path: Path) -> str:
    with Path(path).open("rb") as f:
        return hashlib.file_digest(f, "sha256").hexdigest()


def main(args):
    import torch
    from peft import LoraConfig, get_peft_model
    from transformers import AutoModelForCausalLM, AutoTokenizer

    random.seed(args.seed)
    torch.manual_seed(args.seed)
    started = time.time()
    tokenizer = AutoTokenizer.from_pretrained(args.base, padding_side="left", local_files_only=True)
    no_id, yes_id = (tokenizer.convert_tokens_to_ids(t) for t in ("no", "yes"))

    # 1. 读样本，排除超长的；--limit 用来先拿少量数据把流程跑通。
    data = {}
    for split in SPLITS:
        rows = [r for r in read_jsonl(args.data / f"{split}.jsonl")
                if fits_budget(tokenizer, r, args.max_tokens, args.max_padded_tokens)]
        random.Random(args.seed).shuffle(rows)
        data[split] = rows[: args.limit] if args.limit else rows
    print(json.dumps({"rows": {s: len(v) for s, v in data.items()}}), flush=True)

    model = AutoModelForCausalLM.from_pretrained(args.base, torch_dtype=torch.bfloat16,
                                                 attn_implementation="sdpa", local_files_only=True).to("cuda")

    def score(row):
        """一条样本的 N 个候选一起前向，返回 N 个 yes-no logit 差。"""
        texts = [render(row, i) for i in range(len(row["candidateTools"]))]
        batch = tokenizer(texts, add_special_tokens=False, padding=True, return_tensors="pt").to("cuda")
        # 左填充，所以每段的最后一个 token 都在最后一列；只取这一列的 logits，省显存。
        logits = model(**batch, use_cache=False, logits_to_keep=1).logits[:, -1, [no_id, yes_id]].float()
        return logits[:, 1] - logits[:, 0]

    def evaluate(rows):
        model.eval()
        with torch.inference_mode():
            scored = [{**r, "scores": score(r).cpu().tolist()} for r in rows]
        return metrics(scored)

    # 2. 微调前先测一次基座，作为对照。
    base_test = evaluate(data["test"])
    print(json.dumps({"baseTest": base_test}), flush=True)

    # 3. 挂上 LoRA：只训练注意力和 MLP 里所有线性层旁边的低秩矩阵，基座权重冻结。
    model = get_peft_model(model, LoraConfig(
        r=16, lora_alpha=32, lora_dropout=0.05, bias="none", task_type="CAUSAL_LM",
        target_modules=["q_proj", "k_proj", "v_proj", "o_proj", "gate_proj", "up_proj", "down_proj"]))
    model.gradient_checkpointing_enable(gradient_checkpointing_kwargs={"use_reentrant": False})
    model.enable_input_require_grads()  # 梯度检查点 + 冻结的 embedding 时需要
    trainable = [p for p in model.parameters() if p.requires_grad]
    optimizer = torch.optim.AdamW(trainable, lr=args.learning_rate, weight_decay=0.01)
    model.print_trainable_parameters()

    # 4. 训练循环：一条样本一次前向，累积 args.accumulation 条再更新一次参数。
    train = data["train"]
    total_steps = math.ceil(len(train) / args.accumulation) * args.epochs
    step, seen, loss_sum = 0, 0, 0.0
    for epoch in range(args.epochs):
        random.Random(args.seed + epoch).shuffle(train)
        model.train()
        optimizer.zero_grad()
        for i, row in enumerate(train):
            loss = listwise_loss(torch, score(row), row["targetIndices"])
            # 最后一组可能不满 accumulation 条，按实际条数取平均。
            group = min(args.accumulation, len(train) - (i // args.accumulation) * args.accumulation)
            (loss / group).backward()
            seen, loss_sum = seen + 1, loss_sum + loss.item()
            if (i + 1) % args.accumulation == 0 or i + 1 == len(train):
                torch.nn.utils.clip_grad_norm_(trainable, 1.0)
                step += 1
                for g in optimizer.param_groups:
                    g["lr"] = lr_at(step, total_steps, args.learning_rate)
                optimizer.step()
                optimizer.zero_grad()
                if step % 20 == 0:
                    print(json.dumps({"step": step, "of": total_steps, "meanLoss": loss_sum / seen}), flush=True)
                if args.save_every and step % args.save_every == 0:
                    model.save_pretrained(args.out / f"checkpoint-{step}")

    # 5. 保存 adapter，在验证集和测试集上评测。
    adapter = args.out / "adapter"
    model.save_pretrained(adapter, safe_serialization=True)
    tokenizer.save_pretrained(adapter)
    model.gradient_checkpointing_disable()
    report = {
        # 下面五项是评分服务启动时会校验的字段。
        "status": "complete", "contract": CONTRACT, "rendererSpec": specification(),
        "baseRevision": args.base.name,
        "baseWeightsSha256": {p.name: sha256(p) for p in sorted(args.base.glob("*.safetensors"))},
        "adapterSha256": sha256(adapter / "adapter_model.safetensors"),
        "baseModel": "Qwen/Qwen3-Reranker-4B",
        "method": "BF16 LoRA r16 alpha32, listwise CE over candidates",
        "hyperparameters": {"epochs": args.epochs, "learningRate": args.learning_rate,
                            "accumulation": args.accumulation, "seed": args.seed,
                            "maxTokens": args.max_tokens, "maxPaddedTokens": args.max_padded_tokens},
        "rowsBySplit": {s: len(v) for s, v in data.items()},
        "optimizerSteps": step, "meanLoss": loss_sum / max(1, seen),
        "baseTest": base_test, "validation": evaluate(data["validation"]), "test": evaluate(data["test"]),
        "seconds": round(time.time() - started),
    }
    (args.out / "training-report.json").write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n")
    print(json.dumps({k: report[k] for k in ("baseTest", "test")}))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--data", type=Path, required=True, help="step3 的输出目录")
    parser.add_argument("--base", type=Path, required=True, help="Qwen3-Reranker-4B 快照目录，目录名是 revision")
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--epochs", type=int, default=1)
    parser.add_argument("--learning-rate", type=float, default=2e-5)
    parser.add_argument("--accumulation", type=int, default=8)
    parser.add_argument("--max-tokens", type=int, default=4096)
    parser.add_argument("--max-padded-tokens", type=int, default=16384)
    parser.add_argument("--limit", type=int, default=0, help="每个集合最多取多少条，0 表示全部")
    parser.add_argument("--save-every", type=int, default=0, help="每多少步保存一次 adapter")
    parser.add_argument("--seed", type=int, default=191019)
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    main(args)
