"""Broaden everyday wording of tool requests; scoped synthetic labels."""

import argparse
import json
import random
from pathlib import Path

from .build_domain_decisions import row

CASES = [
    (
        "memory",
        "chat",
        "low",
        "general",
        [
            "你还记得我之前说过，整理小目标时更喜欢怎样做吗？",
            "我以前提过什么休息偏好？帮我回想一下。",
            "上次我说自己如何开始写东西的，你记得吗？",
            "翻翻以前的对话，我曾经说哪些方法对自己有帮助？",
            "之前聊天里，我对这件事有什么想法？",
        ],
    ),
    (
        "dashboard",
        "chat",
        "low",
        "general",
        [
            "看看最近几次对话的心理概况，有哪些情绪变化？",
            "我想看这段时间的心理情况概要。",
            "把几个对话的情绪记录和趋势放在一起看看。",
            "展示我的心理报告和最近的对话统计。",
            "最近的心情在报告里有什么变化？",
        ],
    ),
    (
        "strategy",
        "counseling",
        "low",
        "general",
        [
            "帮我创建一个目标，准备下周的工作汇报，先从写一句话开始。",
            "我想处理这个拖延问题，帮我拆成容易开始的小步骤。",
            "看看我当前的目标做到哪里了，帮我决定接下来试哪一步。",
            "为我制定一个行动计划，开始准备汇报。",
            "带我做一个记录感受的小练习。",
        ],
    ),
    (
        "chat",
        "counseling",
        "low",
        "general",
        [
            "今天特别委屈，暂时不想听建议，只想有人陪我说说。",
            "工作后有些难受，想倾诉一下发生了什么。",
            "我现在心情有点低落，可以听我讲吗？",
            "我觉得有点孤单，想找你聊聊，不用制定方案。",
            "事情进展有些慢，我担心自己做不好，先听我说。",
        ],
    ),
    (
        "chat",
        "chat",
        "low",
        "general",
        [
            "今天看了一部很好看的电影，想随便聊聊。",
            "晚餐我试了一个新菜，味道很不错。",
            "刚出去走了走，天气真舒服。",
            "今天买了新的文具，来和你分享一下。",
            "最近找到了一首喜欢的歌。",
        ],
    ),
    (
        "knowledge",
        "counseling",
        "low",
        "general",
        [
            "解释一下为什么汇报前容易紧张，需要心理知识的出处。",
            "什么是心理学里的压力反应？",
            "能用通俗语言介绍情绪调节的概念吗？",
            "我本人没有自伤想法，只是想理解书里这个词是什么意思。",
            "朋友过去有自伤想法，他现在安全；我想了解怎样支持朋友的通用知识。",
        ],
    ),
    (
        "chat",
        "counseling",
        "elevated",
        "professional",
        [
            "我连续几周都睡不好，已经严重影响工作和生活，想找专业的咨询帮助。",
            "困扰持续了很久，我已无法正常工作，需要专业支持资源。",
            "这几周基本无法上课和休息，我希望尽快获得专业帮助。",
            "生活已经明显受影响，想知道哪里能找到心理咨询师。",
        ],
    ),
    (
        "chat",
        "counseling",
        "urgent",
        "professional",
        [
            "我现在已经准备好了伤害自己的工具，马上要行动。",
            "我已经采取了伤害自己的行动，现在需要立即帮助。",
            "我有今天就行动的自伤计划，工具已经在手边。",
        ],
    ),
    (
        "clarify",
        "chat",
        "low",
        "general",
        [
            "那个东西应该怎么办？我还没说具体是什么。",
            "帮我处理一下，我还没有提供具体问题。",
            "继续吧，但我们刚开始，还没有前面的内容。",
        ],
    ),
]


def additions(count=1200):
    rng = random.Random(821)
    items = []
    for i in range(count):
        route, kind, risk, need, phrases = CASES[i % len(CASES)]
        message = phrases[(i // len(CASES)) % len(phrases)]
        message += f"（记录于12月{1 + i // 1440}日{i // 60 % 24}时{i % 60}分。）"
        state = {"message": message, "history": [], "session_summary": ""}
        # The current explicit request wins over a prior generic topic.
        if i % 4 == 0:
            state["history"] = [
                {"role": "user", "content": "之前我们聊过写作和工作。"},
                {"role": "assistant", "content": "你可以按当前需要，选择聊聊感受或看看记录。"},
            ]
        items.append(
            row(
                "router",
                state,
                {
                    "route": route,
                    "conversation_kind": kind,
                    "risk_band": risk,
                    "support_need": need,
                },
                f"router-natural-{i:05d}",
            )
        )
    rng.shuffle(items)
    parts = {k: [] for k in ["train", "calibration", "validation", "test"]}
    for i, item in enumerate(items):
        split = (
            "train"
            if i < count * 0.7
            else "calibration"
            if i < count * 0.8
            else "validation"
            if i < count * 0.9
            else "test"
        )
        parts[split].append({**item, "split": split, "source": "project_router_requests_v2"})
    return parts


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    for split, extra in additions().items():
        original = [
            json.loads(x)
            for x in (args.base / f"{split}.jsonl").read_text().splitlines()
            if x.strip()
        ]
        (args.out / f"{split}.jsonl").write_text(
            "".join(json.dumps(x, ensure_ascii=False) + "\n" for x in original + extra)
        )
    print(json.dumps({"added": 1200, "out": str(args.out)}))
