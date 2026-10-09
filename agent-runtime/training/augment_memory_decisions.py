"""Convert Memory to focused v2 heads and add natural evidence checks."""

import argparse
import json
import random
from pathlib import Path

from psych_agent_runtime.decision_contract import MEMORY_CONTRACT

from .build_domain_decisions import row
from .decision_data import selected_questions

FACTS = [
    (
        "我更喜欢怎样整理目标的步骤？",
        "我更喜欢先把事情缩成一句话，而不是列很长的清单。",
        "助手建议我一次列很多步骤，我还没决定是否采用。",
    ),
    (
        "我喜欢怎样休息？",
        "我喜欢一个人去散步，不太喜欢人多的地方。",
        "朋友更喜欢去热闹的咖啡店，我只是转述他的偏好。",
    ),
    (
        "我昨天在汇报上实际做了什么？",
        "我昨天打开了文档，写好标题和一句核心观点。",
        "明天我打算准备汇报，今天还没有实际行动。",
    ),
    (
        "我正在准备什么任务？",
        "我现在正在准备下周的工作汇报。",
        "我们讨论过一篇关于焦虑的文章，没有谈具体任务。",
    ),
]


def additions(count=1200):
    rng = random.Random(811)
    result = []
    for i in range(count):
        query, positive, negative = FACTS[(i // 3) % len(FACTS)]
        if i % 3 == 0:
            kind = (i // 12) % 5
            text, labels = [
                (
                    "我更喜欢先把事情缩成一句话，而不是列很长的清单。",
                    {"episodic": "no", "preference": "yes", "procedural": "no", "semantic": "yes"},
                ),
                (
                    "我刚才把汇报的标题写好了，感觉轻松了一些。",
                    {"episodic": "yes", "preference": "no", "procedural": "no", "semantic": "yes"},
                ),
                (
                    "我平时用的方法是先写一句想表达的话，再补一个例子。",
                    {"episodic": "no", "preference": "no", "procedural": "yes", "semantic": "yes"},
                ),
                (
                    "我每周三要参加课程，正在准备下周的工作汇报。",
                    {"episodic": "no", "preference": "no", "procedural": "no", "semantic": "yes"},
                ),
                (
                    "助手建议去散步，我没有去。这只是一个举例。",
                    {"episodic": "no", "preference": "no", "procedural": "no", "semantic": "no"},
                ),
            ][kind]
            state = {"observation": text}
        elif i % 3 == 1:
            n = 1 + (i // 12) % 8
            index = (i // 96) % n
            include = (i // 768) % 2 == 0
            candidates = [
                {
                    "content": positive
                    if include and j == index
                    else negative
                    if j % 2
                    else "我今天买了新的文具，别的事情还没提到。",
                    "role": "user",
                    "observedAt": "2026-10-09T10:00:00",
                    "source": f"fact-{i}-{j}",
                }
                for j in range(n)
            ]
            state = {"query": query, "candidates": candidates}
            labels = {f"relevance_{j}": "yes" if include and j == index else "no" for j in range(n)}
            labels["temporal_need"] = "yes" if "昨天" in query else "no"
        else:
            sufficient = (i // 12) % 2 == 0
            state = {
                "query": query,
                "evidence": [{"content": positive if sufficient else negative, "role": "user"}],
            }
            labels = {"evidence_sufficient": "yes" if sufficient else "no"}
        key = "observation" if "observation" in state else "query"
        state[key] += f"（此条观察时间12月{1 + i // 1440}日{i // 60 % 24}时{i % 60}分。）"
        result.append(row("memory", state, labels, f"memory-natural-{i:05d}"))
    rng.shuffle(result)
    parts = {k: [] for k in ["train", "calibration", "validation", "test"]}
    for i, item in enumerate(result):
        split = (
            "train"
            if i < count * 0.7
            else "calibration"
            if i < count * 0.8
            else "validation"
            if i < count * 0.9
            else "test"
        )
        parts[split].append({**item, "split": split, "source": "project_memory_evidence_v2"})
    return parts


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    for split, more in additions().items():
        original = [
            json.loads(x)
            for x in (args.base / f"{split}.jsonl").read_text().splitlines()
            if x.strip()
        ]
        for x in original:
            x["contractVersion"] = MEMORY_CONTRACT
            x["questions"] = selected_questions("memory", x["state"], list(x["expected"]))
        (args.out / f"{split}.jsonl").write_text(
            "".join(json.dumps(x, ensure_ascii=False) + "\n" for x in original + more)
        )
    print(json.dumps({"added": 1200, "contract": MEMORY_CONTRACT, "out": str(args.out)}))
