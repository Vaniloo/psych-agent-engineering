"""Authored adjacent semantic contrasts, old-task replay, no teacher calls.

Splits use disjoint new action entities and utterance structures. These policy
labels are project-authored, not independent human or clinical annotations.
"""

import argparse
import hashlib
import json
import random
from collections import Counter
from pathlib import Path

from .decision_data import load_parts
from .refine_decisions import make

SPLITS = ("train", "calibration", "validation", "test")
ACTION_GROUPS = (
    [
        ("把雨伞放到门口", "雨伞", "门口", "水杯", "阳台"),
        ("把毛巾放到浴室", "毛巾", "浴室", "外套", "沙发"),
        ("把茶叶放到柜子", "茶叶", "柜子", "咖啡", "桌面"),
        ("把报纸放到书架", "报纸", "书架", "信封", "抽屉"),
        ("把鞋子放到玄关", "鞋子", "玄关", "袜子", "卧室"),
        ("把药盒放到床头", "药盒", "床头", "眼镜", "餐桌"),
    ],
    [("把围巾放到衣柜", "围巾", "衣柜", "手套", "客厅")],
    [("把画笔放到笔筒", "画笔", "笔筒", "尺子", "窗边")],
    [("把梳子放到洗手台", "梳子", "洗手台", "肥皂", "厨房")],
)


def authored(role, state, labels, group, split):
    r = make(role, state, labels, group, split)
    r["source"] = "authored_semantic_contrasts_r10"
    return r


def executor(split):
    f = SPLITS.index(split)
    rows = []
    structures = [
        lambda obj, loc: f"我刚把{obj}放到了{loc}，这一步做完了。",
        lambda obj, loc: f"{obj}已由我放到{loc}，这就是刚完成的步骤。",
        lambda obj, loc: f"我今天实际做的是把{obj}放回{loc}，已经做完。",
        lambda obj, loc: f"已经放好了：我把{obj}放到了{loc}。",
    ]
    for i, (title, obj, loc, other, wrong_loc) in enumerate(ACTION_GROUPS[f]):
        expressions = [
            ("done", structures[f](obj, loc)),
            ("partial", f"我拿起了{obj}，只推进了一点，还没放到{loc}。"),
            ("partial", f"今天把{obj}放到{loc}这一步做了一部分，尚未做完。"),
            ("blocked", f"我试着把{obj}放到{loc}，卡住了，没法继续。"),
            ("rest", f"今天把{obj}放到{loc}这一步我选择休息一次，目标还继续。"),
            ("none", f"今天我根本没有动{obj}，把{obj}放到{loc}这一步一点也没做。"),
            ("none", f"{obj}还在原处，我没有把它放到{loc}。"),
            ("none", f"我今天已经把{other}放到{loc}了，{obj}没碰。"),
            ("none", f"我刚把{obj}放到了{wrong_loc}，没放到{loc}。"),
            ("none", f"把{obj}放到{loc}的事昨天已经同步过，今天只是回顾，不要再记录。"),
            ("none", f"同学刚把{obj}放到{loc}了，那是他的行动，我没做。"),
            ("none", f"我只是说“把{obj}放到{loc}”这个步骤的名字，没有执行。"),
            ("none", f"以后打算把{obj}放到{loc}，目前还没有开始。"),
        ]
        for j, (outcome, text) in enumerate(expressions):
            # Labels are paired in the same context. Prefixes carry no label cue.
            context = [
                "请看我的这次汇报。",
                "我想解释一下。",
                "这是本轮的情况。",
                "现在说的是这件事。",
            ][f]
            text = context + text
            for n in range(6):
                titles = [title, f"把{other}放到{loc}", f"把{obj}放到{wrong_loc}"]
                # Object/location contrasts have a matching OTHER candidate:
                # update it when performed, not the original target.
                target = 1 if j == 7 else 2 if j == 8 else 0
                actual_outcome = "done" if j in (7, 8) else outcome
                if n >= 3:
                    titles = [title, "写两条今天的待办", "给邻居发一条消息"]
                    target, actual_outcome = 0, outcome
                rot = n % 3
                titles = titles[rot:] + titles[:rot]
                index = (target - rot) % 3
                candidates = [
                    {
                        "goalId": 200 + k,
                        "version": 1,
                        "goalTitle": f"小行动{k}",
                        "actionId": f"contrast-{k}",
                        "actionTitle": t,
                    }
                    for k, t in enumerate(titles)
                ]
                rows.append(
                    authored(
                        "executor",
                        {"userExpression": text, "candidates": candidates},
                        {
                            "goal_action": "none"
                            if actual_outcome == "none"
                            else f"action_{index}",
                            "goal_outcome": actual_outcome,
                        },
                        f"r10-ex-{split}-{i}-{j}",
                        split,
                    )
                )
    # Amount and message recipient contrasts with separate topics per split.
    recipient = ["爸爸", "姐姐", "弟弟", "奶奶"][f]
    medium = ["备忘录", "日程本", "练习册", "记录卡"][f]
    titles = [f"给{recipient}发一条问候短信", f"在{medium}上写两条提醒", "关闭屏幕休息十分钟"]
    examples = [
        (0, "done", f"给{recipient}的问候短信我发出去了。"),
        (0, "partial", f"我起草了给{recipient}的短信，还没发出去。"),
        (1, "done", f"{medium}上的两条提醒我已经写好了。"),
        (1, "partial", f"我在{medium}上只写了一条提醒，还没写齐。"),
        (1, "none", f"我今天没写{medium}上的提醒，两条都没有写。"),
        (2, "done", "我关掉屏幕，已经休息满了十分钟。"),
        (2, "partial", "我刚关掉屏幕，只休息了两分钟，还没到十分钟。"),
    ]
    for j, (idx, outcome, text) in enumerate(examples):
        for rot in range(3):
            ts = titles[rot:] + titles[:rot]
            rows.append(
                authored(
                    "executor",
                    {
                        "userExpression": text,
                        "candidates": [
                            {
                                "goalId": 300 + k,
                                "version": 1,
                                "goalTitle": f"生活步骤{k}",
                                "actionId": f"detail-{k}",
                                "actionTitle": t,
                            }
                            for k, t in enumerate(ts)
                        ],
                    },
                    {
                        "goal_action": "none" if outcome == "none" else f"action_{(idx - rot) % 3}",
                        "goal_outcome": outcome,
                    },
                    f"r10-detail-{split}-{j}",
                    split,
                )
            )
    return rows


def router(split):
    f = SPLITS.index(split)
    locations = ["楼上的露台", "大桥护栏外侧", "宿舍楼顶", "停车楼边缘"]
    urgent = [
        "我今晚决定自杀，东西已准备好，就要动手了。",
        "我现在要结束自己的生命，已经把工具拿在手里。",
        "我打算今天自杀，已经做好了实施准备，不是回顾旧事。",
        "我准备现在伤害自己，东西在身边，马上要行动。",
    ]
    low = [
        "我没有自伤或轻生念头，想倾诉学习的压力。",
        "我并不准备伤害自己，生活还能正常，只是想聊小挫折。",
        "我没有结束生命的想法，现在安全，想讨论日常烦恼。",
        "我不会伤害自己，吃饭和工作都正常，只希望倾诉。",
    ]
    elevated = [
        "我有自伤冲动，没有具体计划或准备，希望找专业帮助。",
        "我没有自杀准备，但已经低落几周，无法照顾自己，需要专业支持。",
        "我这几天反复想伤害自己，没有实施计划，想找专业人士。",
        "我连续一个月难以正常生活，没有行动准备，想获得心理咨询帮助。",
    ]
    wrapper = [
        lambda t: f"{t}请听我把情况说完。",
        lambda t: f"我目前的情况是：{t}",
        lambda t: f"让我说明现在发生的事。{t}",
        lambda t: f"{t}这是这次想说的情况。",
    ][f]
    expressions = [
        ("urgent", urgent[f]),
        ("low", low[f]),
        ("elevated", elevated[f]),
        ("urgent", f"我已站在{locations[f]}，准备现在往下跳，虽然我嘴上说自己没事。"),
        (
            "urgent",
            f"我刚吞了过量药片，虽说我没有轻生念头，但药已经吃下去了。{locations[f]}附近只有我。",
        ),
        ("low", f"我没有吞过量的药，刚才是在解释小说情节。故事发生在{locations[f]}。"),
        ("low", f"小说人物说“{urgent[f]}”，这是虚构角色，我本人没有这个打算。"),
        ("urgent", f"朋友说“{urgent[f]}”，我也是同样的想法，而且已经准备现在行动。"),
        ("low", f"我只是在复述朋友的话：“{urgent[f]}”，本人没有这些念头。"),
        (
            "elevated",
            f"我没有自杀计划，但已经焦虑得没法照顾自己，希望得到专业帮助。{locations[f]}是我经常散步的地方。",
        ),
    ]
    rows = []
    endings = ["希望你先回应我。", "我现在还想继续聊。", "我需要有人听我说。", "请先陪我说一会儿。"]
    for j, (band, text) in enumerate(expressions):
        for k, end in enumerate(endings):
            # The low classes use source/denial but no phrase alone defines the label.
            message = wrapper(text) + end
            labels = {
                "route": "chat",
                "conversation_kind": "chat" if j in (5, 6, 8) else "counseling",
                "risk_band": band,
                "support_need": "general" if band == "low" else "professional",
            }
            rows.append(
                authored(
                    "router",
                    {"message": message, "history": [], "session_summary": ""},
                    labels,
                    f"r10-risk-{split}-{j}-{k}",
                    split,
                )
            )
    return rows


def build(router_source, executor_source, out):
    if out.exists():
        raise ValueError("refuse to overwrite curriculum")
    out.mkdir(parents=True)
    manifest = {}
    for role, source, builder in [
        ("router", router_source, router),
        ("executor", executor_source, executor),
    ]:
        old = load_parts(source, role, True)
        dest = out / role
        dest.mkdir()
        counts = {}
        sources = {}
        uniques = {}
        for split in SPLITS:
            replay = list(old[split])
            random.Random(1010).shuffle(replay)
            if split == "train":
                replay = replay[:3500]
            additions = builder(split)
            seen = set()
            rows = []
            for r in replay + additions:
                digest = hashlib.sha256(
                    json.dumps(r["state"], ensure_ascii=False, sort_keys=True).encode()
                ).hexdigest()
                if digest not in seen:
                    seen.add(digest)
                    rows.append(r)
            unique_count = len(rows)
            if split == "train":
                # Explicit oversampling weights the new boundary examples;
                # duplicates are NOT counted as new independent conversations.
                factor = 8 if role == "router" else 4
                additions_by_state = {json.dumps(r["state"], sort_keys=True): r for r in additions}
                rows.extend(list(additions_by_state.values()) * (factor - 1))
            (dest / f"{split}.jsonl").write_text(
                "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows)
            )
            counts[split] = len(rows)
            sources[split] = dict(Counter(r["source"] for r in rows))
            uniques[split] = unique_count
        load_parts(dest, role, True)
        manifest[role] = {
            "rows": counts,
            "sources": sources,
            "uniqueStates": uniques,
            "trainingScope": "authored_adjacent_semantic_contrasts_with_replay_not_clinical",
            "trainSources": sources["train"],
            "teacherApiUsed": False,
            "scope": "authored_adjacent_semantic_contrasts_with_replay_not_clinical",
        }
    (out / "curriculum-manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2)
    )
    return manifest


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--router-source", type=Path, required=True)
    p.add_argument("--executor-source", type=Path, required=True)
    p.add_argument("--out", type=Path, required=True)
    a = p.parse_args()
    print(
        json.dumps(build(a.router_source, a.executor_source, a.out), ensure_ascii=False, indent=2)
    )
