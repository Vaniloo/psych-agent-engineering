"""全流程共用：样本格式、候选渲染、损失和指标。

一条训练样本（step3 产出）长这样：

    {"id": "...", "split": "train",
     "context": [{"role": "user", "content": "..."}, {"role": "tool_call", ...}, ...],
     "candidateTools": [{"kind": "tool", "tool": {...}}, ..., TEXT],
     "targetIndices": [2],                 # 示范里下一步选了哪个候选
     "observedAction": "tool_call"}        # 或 "assistant_text"

模型对每个候选单独打一个分，分数 = "yes" 的 logit - "no" 的 logit。
"""

from __future__ import annotations

import json
from pathlib import Path

# 渲染函数和线上评分服务用的是同一个，训练和推理的输入格式因此完全一致。
from psych_agent_runtime.public_tool_contract import CONTRACT, TEXT, render, specification

SPLITS = ("train", "validation", "test")

__all__ = ["CONTRACT", "SPLITS", "TEXT", "listwise_loss", "metrics", "read_jsonl", "render",
           "specification", "top1_correct", "write_jsonl"]


def read_jsonl(path: Path) -> list[dict]:
    with Path(path).open() as f:
        return [json.loads(line) for line in f if line.strip()]


def write_jsonl(path: Path, rows) -> int:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    with path.open("w") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
            count += 1
    return count


def listwise_loss(torch, scores, targets: list[int]):
    """所有候选的分数一起做 softmax，最大化示范动作的概率（listwise 交叉熵）。

    示范里同一步调用了多个工具时，每个被选中的工具平均分摊监督信号。
    没被选中的候选只是“这次没选”，不当作“这个工具没用”的负标签。
    """
    return -torch.log_softmax(scores, dim=0)[targets].mean()


def top1_correct(scores: list[float], targets: list[int]) -> bool:
    best = max(range(len(scores)), key=scores.__getitem__)
    return best in targets


def metrics(rows: list[dict]) -> dict:
    """Top-1 一致率：分数最高的候选是否就是示范里的下一步。按动作类型分别统计。"""
    def agreement(part):
        if not part:
            return {"rows": 0, "top1": None}
        return {"rows": len(part),
                "top1": sum(top1_correct(r["scores"], r["targetIndices"]) for r in part) / len(part)}

    return {"all": agreement(rows),
            "toolCall": agreement([r for r in rows if r["observedAction"] == "tool_call"]),
            "assistantText": agreement([r for r in rows if r["observedAction"] == "assistant_text"])}
