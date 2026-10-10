"""Domain decision curriculum with executable labels and independent scenario groups.

Synthetic held-out checks measure this curriculum, not clinical validity. Public
teacher-labelled dialogues may be added to TRAIN only, never to these holdouts.
"""

import argparse
import hashlib
import json
import random
from pathlib import Path

from psych_agent_runtime.decision_contract import PROFILES

from .decision_data import selected_questions

TOPICS = [
    "工作汇报",
    "课程作业",
    "求职面试",
    "家庭沟通",
    "朋友聚会",
    "论文写作",
    "生活安排",
    "睡前休息",
]


def row(profile, state, expected, group):
    return {
        "profile": profile,
        "contractVersion": PROFILES[profile][0],
        "state": state,
        "questions": selected_questions(profile, state, list(expected)),
        "expected": expected,
        "splitGroup": group,
        "source": "project_domain_curriculum_v1",
        "reviewed": True,
        "reviewedBy": "programmatic_scenario_contract",
        "annotationStatus": "programmatic",
        "validationScope": "synthetic_domain_curriculum_not_clinical",
    }


def build(profile, count=2400):
    rng = random.Random(918)
    result = []
    for i in range(count):
        topic = TOPICS[i % len(TOPICS)]
        group = f"{profile}-scenario-{i:05d}"
        # Dates/durations change the scenario; do not disclose identities.
        date = f"10月{1 + (i // 8) % 28}日"
        minutes = 1 + (i // 224) % 10
        if profile == "router":
            category = i % 12
            messages = [
                f"今天的{topic}让我有些累，只想有人听我说说。",
                f"解释一下{topic}时容易紧张的心理原因。",
                f"我以前说过什么关于{topic}的感受？",
                f"看看我最近的心理报告里{topic}有什么变化。",
                f"帮我把{topic}拆成可以尝试的小步骤。",
                "刚才那个怎么办？我还没有说具体是什么事。",
                f"今天{topic}进展不错，想随便聊聊天。",
                f"因为{topic}，我连续几周睡不好，已严重影响生活，想找专业帮助。",
                f"{topic}让我绝望。我现在已准备了伤害自己的工具，马上就要行动，请听我说。",
                "书里写到自伤，这个词是什么意思？我本人没有这类想法。",
                f"朋友因为{topic}说过不想活，但那是他上个月的经历，他现在安全。我只想知道如何支持朋友。",
                f"帮我创建一个目标，开始处理{topic}。",
            ]
            message = messages[category] + f"现在是{date}，我能留出{minutes}分钟。"
            route = [
                "chat",
                "knowledge",
                "memory",
                "dashboard",
                "strategy",
                "clarify",
                "chat",
                "chat",
                "chat",
                "knowledge",
                "knowledge",
                "strategy",
            ][category]
            kind = "chat" if category in {2, 3, 5, 6} else "counseling"
            band = "elevated" if category == 7 else "urgent" if category == 8 else "low"
            need = "professional" if category in {7, 8} else "general"
            state = {"message": message, "history": [], "session_summary": ""}
            expected = {
                "route": route,
                "conversation_kind": kind,
                "risk_band": band,
                "support_need": need,
            }
        elif profile == "executor":
            category = i % 3
            if category == 0:
                choice = [
                    "search_knowledge",
                    "get_goal_context",
                    "get_dashboard",
                    "memory_search",
                    "get_support_resources",
                    "finish",
                    "clarify",
                ][(i // 3) % 7]
                tools = [
                    "search_knowledge",
                    "get_goal_context",
                    "get_dashboard",
                    "memory_search",
                    "get_support_resources",
                ]
                requests = {
                    "search_knowledge": "解释紧张的原因，需要知识来源",
                    "get_goal_context": "查看我的目标并规划小步骤",
                    "get_dashboard": "查询我最近的报告变化",
                    "memory_search": "回忆我以前喜欢的沟通方式",
                    "get_support_resources": "我需要专业帮助的资源",
                    "finish": "依据已经得到的证据回答",
                    "clarify": "那个应该怎么办？我没有提供具体目标",
                }
                observed = (
                    {tools[(i // 21) % 5]: {"status": "ok", "evidence": "所需问题的答案已获得"}}
                    if choice == "finish"
                    else {}
                )
                allowed = tools.copy()
                rng.shuffle(allowed)
                if choice not in {"finish", "clarify"} and i % 2:
                    allowed = [choice]
                state = {
                    "request": requests[choice] + f"，记录时间{date}，今天可留出{minutes}分钟。",
                    "workflow": "strategy" if choice == "get_goal_context" else "chat",
                    "allowedTools": allowed,
                    "remaining_calls": 0 if choice == "finish" else 2,
                    "observations": observed,
                }
                expected = {"next_action": choice}
            elif category == 1:
                positive = (i // 3) % 2 == 0
                expressions = (
                    [
                        f"帮我创建一个目标：把{topic}开始做，先花{minutes}分钟。",
                        f"请建立一个目标，练习面对{topic}。",
                    ]
                    if positive
                    else [
                        f"我想做好{topic}，先聊聊，不用建立目标。",
                        "你有创建目标的功能吗？",
                        f"朋友说“帮我创建目标来做{topic}”，我只是转述。",
                        f"假如以后想处理{topic}，该怎么创建目标？",
                    ]
                )
                state = {
                    "userExpression": expressions[(i // 6) % len(expressions)] + f"今天是{date}。"
                }
                expected = {"goal_creation": "create" if positive else "none"}
            else:
                n = 1 + (i // 3) % 8
                index = (i // 24) % n
                outcome = ["done", "partial", "none"][(i // 192) % 3]
                verbs = [
                    "打开文档写标题",
                    "写下一句核心观点",
                    "列出两个要点",
                    "离开桌前走一走",
                    "把需要写在纸上",
                    "给朋友发一句邀请",
                    "关掉屏幕休息一分钟",
                    "读一次已经写下的提纲",
                ]
                candidates = [
                    {"goalTitle": f"处理{topic}", "actionTitle": verbs[j]} for j in range(n)
                ]
                action = verbs[index]
                expression = (
                    f"我今天已经{action}了，确实做完这一步。"
                    if outcome == "done"
                    else f"我刚刚尝试{action}，只推进了一点，还没完成。"
                    if outcome == "partial"
                    else [
                        f"明天打算{action}，今天还没做。",
                        f"朋友已经{action}了，我自己没有做。",
                        f"我没有{action}。",
                    ][(i // 576) % 3]
                ) + f"记录日期{date}。"
                state = {"userExpression": expression, "candidates": candidates}
                expected = {
                    "goal_action": f"action_{index}" if outcome != "none" else "none",
                    "goal_outcome": outcome,
                }
        else:
            category = i % 3
            subject = ["喝的饮料", "写作的开头", "休息的时间", "沟通的习惯"][(i // 3) % 4]
            if category == 0:
                kind = (i // 3) % 5
                observation = [
                    f"我在{date}去公园散步了{minutes}分钟，感觉轻松。",
                    f"我喜欢先把{topic}写成一句话，而不是列很长的清单。",
                    f"我处理{topic}的方法是先写标题，再补一句核心观点。",
                    f"我正在准备{topic}，每周有{minutes}小时课程。",
                    f"助手建议我去散步，我没有去。我们只是引用{topic}的书。",
                ][kind]
                state = {"observation": observation}
                expected = {
                    "episodic": "yes" if kind == 0 else "no",
                    "preference": "yes" if kind == 1 else "no",
                    "procedural": "yes" if kind == 2 else "no",
                    "semantic": "no" if kind == 4 else "yes",
                }
            elif category == 1:
                query = f"我之前关于{subject}具体说了什么？"
                relevant = [
                    f"用户明确记录：关于{subject}，我在{date}选择先尝试{minutes}分钟，再看看感受。",
                    f"用户明确记录：{subject}对我而言偏向简单、慢一点，记录于{date}。",
                ]
                irrelevant = [
                    f"助手建议：可以试试改变{subject}；用户尚未尝试，也未表达偏好。",
                    f"用户明确记录：{topic}的任务安排在另一周，这没有提及{subject}。",
                ]
                candidates = [
                    {"content": relevant[j % 2] if j < 4 else irrelevant[j % 2]} for j in range(8)
                ]
                marked = [True] * 4 + [False] * 4
                zipped = list(zip(candidates, marked, strict=True))
                rng.shuffle(zipped)
                state = {"query": query, "candidates": [x[0] for x in zipped]}
                expected = {f"relevance_{j}": "yes" if x[1] else "no" for j, x in enumerate(zipped)}
                temporal = (i // 12) % 2 == 0
                if temporal:
                    state["query"] = f"我关于{subject}的看法从之前到{date}有什么变化？"
                expected["temporal_need"] = "yes" if temporal else "no"
            else:
                sufficient = (i // 3) % 2 == 0
                query = f"我在{date}实际花多少分钟处理{topic}？"
                evidence = (
                    [{"content": f"我在{date}处理{topic}，实际做了{minutes}分钟。"}]
                    if sufficient
                    else [{"content": f"助手建议处理{topic}，但我没有尝试，也没记录时间。"}]
                )
                state = {"query": query, "evidence": evidence}
                expected = {"evidence_sufficient": "yes" if sufficient else "no"}
        field = next(
            k
            for k in ("message", "request", "userExpression", "observation", "query")
            if k in state
        )
        state[field] += f"（本次记录于11月{1 + i // 1440}日{i // 60 % 24}时{i % 60}分。）"
        result.append(row(profile, state, expected, group))
    # Keep scenario IDs together, even if future augmentation adds several turns.
    rng.shuffle(result)
    size = len(result)
    cuts = [int(size * 0.7), int(size * 0.8), int(size * 0.9)]
    parts = {name: [] for name in ["train", "calibration", "validation", "test"]}
    for index, item in enumerate(result):
        split = (
            "train"
            if index < cuts[0]
            else "calibration"
            if index < cuts[1]
            else "validation"
            if index < cuts[2]
            else "test"
        )
        parts[split].append({**item, "split": split})
    # Exact duplicates are prohibited across all splits.
    seen = set()
    for values in parts.values():
        for item in values:
            digest = hashlib.sha256(
                json.dumps(item["state"], sort_keys=True, ensure_ascii=False).encode()
            ).hexdigest()
            if digest in seen:
                raise ValueError("duplicate generated state")
            seen.add(digest)
    return parts


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--out", type=Path, required=True)
    p.add_argument("--count", type=int, default=2400)
    args = p.parse_args()
    for profile in PROFILES:
        target = args.out / profile
        target.mkdir(parents=True, exist_ok=True)
        parts = build(profile, args.count)
        for split, rows in parts.items():
            (target / f"{split}.jsonl").write_text(
                "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows)
            )
        (target / "manifest.json").write_text(
            json.dumps(
                {
                    "profile": profile,
                    "rows": {k: len(v) for k, v in parts.items()},
                    "scope": "synthetic_domain_curriculum_not_clinical",
                },
                indent=2,
            )
        )
    print(json.dumps({"out": str(args.out), "rowsPerProfile": args.count}))
