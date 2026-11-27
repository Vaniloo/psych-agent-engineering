"""第 3 步：把每段对话拆成“下一步动作”样本，并按分组切分训练 / 验证 / 测试集。

一段对话里，助手每次行动（调用工具或说一段话）都生成一条样本：
- 输入：这次行动之前的全部事件（目标行动本身和它的参数都不能出现在输入里）；
- 候选：这段对话可用的所有工具 + TEXT（“现在直接回复”）；
- 标签：示范里实际选了哪个候选。

例：用户问天气 → 助手调用 Get Weather → 工具返回 → 助手回复
    样本 1：context=[用户]，              target=Get Weather
    样本 2：context=[用户, 调用, 返回]，  target=TEXT

切分：只要两段对话共享任何一个工具名，或者首句提问相同，就必须进同一个集合。
否则模型可能在训练集里见过“这个工具该什么时候用”，测试分数就虚高了。

    python -m training.lora.step3_samples --episodes work/episodes.jsonl --out work/samples
"""

import argparse
import hashlib
import json
import unicodedata
from collections import Counter, defaultdict
from pathlib import Path

from .common import SPLITS, TEXT, read_jsonl, write_jsonl

RATIOS = {"train": 0.8, "validation": 0.1, "test": 0.1}


def _key(value) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode()).hexdigest()[:16]


def _normalize(text: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", text).casefold().split())


def samples(episode: dict) -> list[dict]:
    candidates = [{"kind": "tool", "tool": t} for t in episode["tools"]] + [TEXT]
    index = {t["name"]: i for i, t in enumerate(episode["tools"])}
    history, out = [], []
    for turn, event in enumerate(episode["events"]):
        if event["role"] in {"tool_call", "assistant"}:
            if not any(e["role"] == "user" for e in history):
                raise ValueError("assistant_action_before_user")
            if event["role"] == "tool_call":
                # 并行调用多个工具时，每个被调用的工具都是正确答案
                targets = sorted({index[c["name"]] for c in event["calls"]})
                action = "tool_call"
            else:
                targets, action = [len(candidates) - 1], "assistant_text"  # TEXT 永远是最后一个候选
            out.append({"id": _key([episode["id"], turn]), "episodeId": episode["id"], "turn": turn,
                        "context": [dict(e) for e in history], "candidateTools": candidates,
                        "targetIndices": targets, "observedAction": action})
        history.append({"role": event["role"], "content": event["content"]})
    return out


def split_by_group(episodes: list[dict], seed: int = 191019) -> dict[str, str]:
    """并查集：共享工具名或首句提问的对话连成一个分组，整组只进一个集合。"""
    parent: dict[str, str] = {}

    def find(x):
        parent.setdefault(x, x)
        while parent[x] != x:
            parent[x] = parent[parent[x]]  # 路径压缩
            x = parent[x]
        return x

    def keys(episode):
        first = next(e["content"] for e in episode["events"] if e["role"] == "user")
        return ["tool:" + _normalize(t["name"]) for t in episode["tools"]] + ["query:" + _normalize(first)]

    for episode in episodes:
        k = keys(episode)
        for other in k[1:]:
            a, b = find(k[0]), find(other)
            if a != b:
                parent[max(a, b)] = min(a, b)
    groups = defaultdict(list)
    for episode in episodes:
        groups[find(keys(episode)[0])].append(episode["id"])
    # 大组先分配，每次放进离目标比例差得最多的集合；同样大小的组用 seed 打散顺序。
    target = {s: len(episodes) * RATIOS[s] for s in SPLITS}
    filled, assignment = Counter(), {}
    for root, members in sorted(groups.items(), key=lambda g: (-len(g[1]), _key([seed, g[0]]))):
        split = max(SPLITS, key=lambda s: (target[s] - filled[s]) / target[s])
        filled[split] += len(members)
        assignment.update(dict.fromkeys(members, split))
    return assignment


def run(episodes_path: Path, out: Path, seed: int = 191019) -> dict:
    episodes, seen = [], set()
    for episode in read_jsonl(episodes_path):
        identity = _key([episode["tools"], episode["events"]])
        if identity not in seen and episode["tools"]:  # 去重；没有工具的对话没有可学的选择
            seen.add(identity)
            episodes.append(episode)
    assignment = split_by_group(episodes, seed)
    rows = {s: [] for s in SPLITS}
    for episode in episodes:
        for row in samples(episode):
            rows[assignment[episode["id"]]].append({**row, "split": assignment[episode["id"]]})
    stats = {"episodes": len(episodes),
             "samples": {s: write_jsonl(Path(out) / f"{s}.jsonl", rows[s]) for s in SPLITS},
             "actions": {s: dict(Counter(r["observedAction"] for r in rows[s])) for s in SPLITS}}
    (Path(out) / "stats.json").write_text(json.dumps(stats, indent=2) + "\n")
    print(json.dumps(stats))
    return stats


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--episodes", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--seed", type=int, default=191019)
    args = parser.parse_args()
    run(args.episodes, args.out, args.seed)
