"""Add progress paraphrases and post-tool transitions; synthetic, not human gold."""

import argparse
import json
import random
from pathlib import Path

from .build_domain_decisions import row

# Each phrase describes exactly the paired step. Partial reports keep it unfinished.
ACTIONS = [
    ("打开文档写标题", "把题目写到了文档最上面", "文档开好了，题目只敲了两个字"),
    ("写下一句核心观点", "写好了最想表达的那句话", "核心观点只写出开头几个词，还没连成一句话"),
    ("列出两个要点", "把两个重点都列出来了", "两个重点目前只列了一个"),
    ("离开桌前走一走", "离开电脑，在走廊来回走了一趟", "刚起身走了一小段就回来了"),
    ("把需要写在纸上", "把自己的需要完整地写在纸上了", "在纸上写了开头，自己的需要还没表达清楚"),
    ("给朋友发一句邀请", "把邀请的那条消息发给朋友了", "邀请消息写了一半，还没有发出去"),
    ("关掉屏幕休息一分钟", "关掉屏幕，安静休息满一分钟了", "关屏休息了十秒钟，还不到一分钟"),
    (
        "读一次已经写下的提纲",
        "把写好的提纲从头到尾读过一遍了",
        "提纲只读了开头，还没有从头到尾读完",
    ),
    ("写三个汇报要点", "汇报的三个重点已经都写好了", "汇报的三个重点，目前只写出了一个"),
    ("练习两分钟开场", "开场练习了完整两分钟", "开场只练了十几秒就停了，没练到两分钟"),
]


def additions(count=1200):
    rng = random.Random(2048)
    result = []
    for i in range(count):
        if i % 3:
            title, done, partial = ACTIONS[(i // 3) % len(ACTIONS)]
            outcome = ["done", "partial", "blocked", "rest", "none"][(i // 30) % 5]
            expression = {
                "done": f"我刚才{done}，这个小步骤已经做完了。",
                "partial": f"我今天已经开始了：{partial}，只推进了一点，不能算完成。",
                "blocked": f"我刚才尝试{title}，但是卡住了，暂时做不下去。",
                "rest": f"我今天先不继续{title}了，已经决定这次先休息，目标还会继续。",
                "none": f"明天想试试{title}，今天还没行动。",
            }[outcome]
            count_candidates = 1 + i % 8
            others = [x[0] for x in ACTIONS if x[0] != title]
            rng.shuffle(others)
            options = [title, *others[: count_candidates - 1]]
            rng.shuffle(options)
            candidates = [
                {
                    "goalTitle": "准备工作汇报" if j % 2 else "尝试一个小行动",
                    "actionTitle": option,
                    "goalId": j + 1,
                    "version": 0,
                    "actionId": f"step-{j}",
                }
                for j, option in enumerate(options)
            ]
            state = {"userExpression": expression, "candidates": candidates}
            labels = {
                "goal_action": f"action_{options.index(title)}" if outcome != "none" else "none",
                "goal_outcome": outcome,
            }
        else:
            tool = ["memory_search", "get_goal_context", "get_dashboard", "search_knowledge"][
                (i // 3) % 4
            ]
            query, value = {
                "memory_search": (
                    "我以前说更喜欢哪种休息方式？",
                    {
                        "evidence": [{"role": "user", "content": "我更喜欢一个人散步。"}],
                        "status": "sufficient",
                        "controller": "laya",
                    },
                ),
                "get_goal_context": (
                    "看看我的目标现在进展怎样，再给我一个建议。",
                    {
                        "goals": [
                            {
                                "title": "准备工作汇报",
                                "actions": [{"title": "写一句核心观点", "status": "planned"}],
                            }
                        ]
                    },
                ),
                "get_dashboard": (
                    "最近几次对话的心理概要有什么变化？",
                    {"summary": "最近谈到工作压力，已有两次对话记录。", "sessions": 2},
                ),
                "search_knowledge": (
                    "解释一下为什么临近汇报会紧张，需要出处。",
                    {
                        "hits": [
                            {
                                "title": "压力与应对",
                                "text": "压力可能与对任务的评价有关。",
                                "source": "本地心理知识",
                            }
                        ]
                    },
                ),
            }[tool]
            answered = (i // 12) % 2 == 0
            permissions = (
                [tool]
                if not answered
                else ["search_knowledge"]
                if tool != "search_knowledge" and i % 4 == 0
                else []
            )
            state = {
                "request": query,
                "workflow": {
                    "memory_search": "memory",
                    "get_goal_context": "strategy",
                    "get_dashboard": "dashboard",
                    "search_knowledge": "knowledge",
                }[tool],
                "allowedTools": permissions,
                "remaining_calls": 1 if answered else 2,
                "observations": {tool: json.dumps(value, ensure_ascii=False)} if answered else {},
            }
            labels = {"next_action": "finish" if answered else tool}
        key = "userExpression" if "userExpression" in state else "request"
        state[key] += f"（本条记录时间12月{1 + i // 1440}日{i // 60 % 24}时{i % 60}分。）"
        result.append(row("executor", state, labels, f"executor-paraphrase-{i:05d}"))
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
        parts[split].append({**item, "split": split, "source": "project_executor_paraphrases_v2"})
    return parts


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--base", type=Path, required=True)
    p.add_argument("--out", type=Path, required=True)
    args = p.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    for split, more in additions().items():
        original = [
            json.loads(x)
            for x in (args.base / f"{split}.jsonl").read_text().splitlines()
            if x.strip()
        ]
        (args.out / f"{split}.jsonl").write_text(
            "".join(json.dumps(x, ensure_ascii=False) + "\n" for x in original + more)
        )
    print(json.dumps({"added": 1200, "out": str(args.out)}))
