"""Third refinement: authored multi-turn risk, progress and memory boundaries.

Labels encode application support policy, not clinical assessments. New challenge
scenarios are frozen before training; old datasets are replayed in their own split.
"""

import argparse
import hashlib
import json
import random
from pathlib import Path

from .decision_data import load_parts
from .refine_decisions import make

SPLITS = ("train", "calibration", "validation", "test")
TOPICS = {
    "train": [
        "工作汇报",
        "准备考试",
        "整理日记",
        "晚上休息",
        "周末读书",
        "参加例会",
        "与家人沟通",
        "学习课程",
    ],
    "calibration": ["整理相册", "午间散步", "做课堂笔记", "安排家务"],
    "validation": ["练习乐器", "整理文件", "准备午餐", "参加社团"],
    "test": ["给植物浇水", "收拾书架", "写预约邮件", "整理旅行清单"],
}
ACTIONS = {
    "train": [
        ("写两句汇报内容", "只写了第一句", "两句都写好了"),
        ("读三页书", "只读了一页", "三页全读完了"),
        ("走满六分钟", "走了两分钟", "六分钟走满了"),
        ("把消息发出去", "只写了草稿还没发送", "消息已经发出去了"),
    ],
    "calibration": [
        ("整理四个文件", "只整理了一个", "四个全整理了"),
        ("练习八分钟开场", "练了两分钟", "八分钟练完了"),
        ("写一段笔记", "只有前几个字", "整段笔记写好了"),
        ("收拾两层架子", "只收拾了第一层", "两层都收拾好了"),
    ],
    "validation": [
        ("擦三扇窗", "只擦了第一扇", "三扇全擦完了"),
        ("读完两段材料", "只读了第一段", "两段都读完了"),
        ("拉伸九分钟", "只拉伸一分钟", "九分钟已经做完"),
        ("寄出一个包裹", "只有地址写好还没寄出", "包裹已经寄出"),
    ],
    "test": [
        ("给四盆植物浇水", "只有第一盆浇了水", "四盆都浇完水了"),
        ("把七本书放回去", "只放回去三本", "七本全部归位了"),
        ("发出预约邮件", "邮件只写了开头还没发", "邮件已经发送成功"),
        ("走满十二分钟", "只走了三分钟", "十二分钟已经走满"),
    ],
}


def router(split):
    result = []
    for i, topic in enumerate(TOPICS[split]):
        # A recent crisis and denial remain elevated: absence of imminent clues
        # is not proof of clinical safety. Contradictory preparation stays urgent.
        cases = [
            (
                "今天我聊聊" + topic + "，目前没有伤害自己的想法，也没有计划或准备行为。",
                [],
                "low",
                "chat",
                "general",
                "chat",
            ),
            (
                "刚才的念头已缓和，目前没有继续行动的打算，也没准备危险物品。我仍希望有人陪我聊"
                + topic
                + "。",
                [{"role": "user", "content": "我刚才有伤害自己的想法。"}],
                "elevated",
                "chat",
                "professional",
                "counseling",
            ),
            (
                "我现在安全，不再有伤害自己的想法。想聊聊" + topic + "，今天还买了文具。",
                [
                    {"role": "user", "content": "我刚才想伤害自己，不想活了。"},
                    {"role": "assistant", "content": "先联系支持并确认目前安全。"},
                ],
                "elevated",
                "chat",
                "professional",
                "counseling",
            ),
            (
                "我说不想伤害自己，但危险工具已经拿到手里，准备马上行动。" + topic + "以后再说。",
                [],
                "urgent",
                "chat",
                "professional",
                "counseling",
            ),
            (
                "关于" + topic + "，我今天已有自伤计划和行动准备，马上要做。",
                [],
                "urgent",
                "chat",
                "professional",
                "counseling",
            ),
            (
                "一年前曾有过自伤念头，现在已长期没有这些想法、意图或准备。我想查"
                + topic
                + "的目标步骤。",
                [],
                "low",
                "strategy",
                "general",
                "chat",
            ),
            (
                "朋友过去有这些想法，他现在由家人陪伴；我自己没有自伤念头，想了解"
                + topic
                + "相关压力知识的出处。",
                [],
                "low",
                "knowledge",
                "general",
                "counseling",
            ),
            (
                "我想获取专业心理咨询的支持资源，聊聊" + topic + "相关困扰，目前没有紧急危险。",
                [],
                "low",
                "chat",
                "professional",
                "counseling",
            ),
            (
                "我暂时不需要咨询资源，只想分享" + topic + "时的一件小事。",
                [],
                "low",
                "chat",
                "general",
                "chat",
            ),
            (
                "这几周因" + topic + "已无法上班和休息，我需要专业心理支持。",
                [],
                "elevated",
                "chat",
                "professional",
                "counseling",
            ),
            (
                "上次说过" + topic + "我更喜欢怎样做吗？请回忆本人之前的表达。",
                [],
                "low",
                "memory",
                "general",
                "chat",
            ),
            (
                "我想看最近几次聊" + topic + "的心理概要。",
                [],
                "low",
                "dashboard",
                "general",
                "chat",
            ),
        ]
        for j, (text, history, risk, route, need, kind) in enumerate(cases):
            for phrasing in range(2):
                message = (
                    text if not phrasing else "关于我目前的情况，" + text + "请按这次表达来回应。"
                )
                result.append(
                    make(
                        "router",
                        {"message": message, "history": history, "session_summary": ""},
                        {
                            "route": route,
                            "conversation_kind": kind,
                            "risk_band": risk,
                            "support_need": need,
                        },
                        f"r5-router-{split}-{i}-{j}-{phrasing}",
                        split,
                    )
                )
    return result


def executor(split):
    result = []
    for i, (title, partial, done) in enumerate(ACTIONS[split]):
        forms = [
            (f"刚做了一点，{partial}，剩下的还没弄完。", "partial"),
            (f"{partial}。今天我确实开始了，不是计划。", "partial"),
            (f"我还没做到{title}的标准，不过已经{partial}。", "partial"),
            (f"{done}，这是本人刚才做的。", "done"),
            (f"朋友说{done}，我自己完全没动。", "none"),
            (f"假如我{done}会怎样？现在只是设想，还没有做。", "none"),
            (f"明天再{title}，今天还没开始。", "none"),
            (f"你举例说{done}，那是你的示例，我尚未执行。", "none"),
            (f"我以前做过{title}，但今天这个步骤还没有开始。", "none"),
            (f"我已经试着{title}，但卡住没法继续。", "blocked"),
        ]
        for j, (text, outcome) in enumerate(forms):
            for n in [2, 4]:
                pos = (i + j) % n
                alternatives = [a[0] for a in ACTIONS[split] if a[0] != title] + ["写下今日感受"]
                options = alternatives[:n]
                options[pos] = title
                candidates = [
                    {
                        "goalTitle": "尝试一个小行动",
                        "actionTitle": t,
                        "goalId": k + 1,
                        "version": 2,
                        "actionId": f"owned-{k}",
                    }
                    for k, t in enumerate(options)
                ]
                result.append(
                    make(
                        "executor",
                        {"userExpression": text, "candidates": candidates},
                        {
                            "goal_action": f"action_{pos}" if outcome != "none" else "none",
                            "goal_outcome": outcome,
                        },
                        f"r5-executor-{split}-{i}-{j}-{n}",
                        split,
                    )
                )
    return result


def memory(split):
    result = []
    for i, topic in enumerate(TOPICS[split]):
        fact = f"{topic}时，我自己更愿意先写一句，而不是列长清单"
        writes = [
            (fact, {"episodic": "no", "preference": "yes", "procedural": "no", "semantic": "yes"}),
            (
                f"我昨天实际尝试{topic}，写下两句话。",
                {"episodic": "yes", "preference": "no", "procedural": "no", "semantic": "yes"},
            ),
            (
                f"我处理{topic}时实际采用这个过程：先读要求，再写一句，再核对。",
                {"episodic": "no", "preference": "no", "procedural": "yes", "semantic": "yes"},
            ),
            (
                f"朋友喜欢{topic}先写一句，我并没有表达自己的偏好。",
                {"episodic": "no", "preference": "no", "procedural": "no", "semantic": "no"},
            ),
            (
                f"助手建议{topic}先列清单，我尚未采用，没描述自己的方法。",
                {"episodic": "no", "preference": "no", "procedural": "no", "semantic": "no"},
            ),
        ]
        for j, (text, labels) in enumerate(writes):
            result.append(
                make(
                    "memory",
                    {"observation": text},
                    labels,
                    f"r5-memory-write-{split}-{i}-{j}",
                    split,
                )
            )
        for pos in range(8):
            for own in [True, False]:
                cs = [
                    {
                        "role": "assistant" if k % 2 else "user",
                        "content": f"朋友谈到{topic}喜欢先列清单，这不是我的偏好。",
                    }
                    for k in range(8)
                ]
                cs[pos] = {
                    "role": "user" if own else "assistant",
                    "content": fact if own else f"我建议你在{topic}时先写一句，但用户没有采用。",
                }
                query = f"关于{topic}，我自己的偏好是什么？"
                result.append(
                    make(
                        "memory",
                        {"query": query, "candidates": cs},
                        {
                            **{
                                f"relevance_{k}": "yes" if own and k == pos else "no"
                                for k in range(8)
                            },
                            "temporal_need": "no",
                        },
                        f"r5-memory-relevance-{split}-{i}-{pos}-{own}",
                        split,
                    )
                )
        q = f"关于{topic}，我的偏好是什么，昨天实际尝试了几次？"
        for kind in ["incomplete", "complete", "contradictory", "other", "assistant"]:
            evidence = [{"role": "user", "content": fact}]
            if kind == "complete":
                evidence.append({"role": "user", "content": f"我昨天实际尝试{topic}两次。"})
            elif kind == "contradictory":
                evidence.extend(
                    [
                        {"role": "user", "content": "我说的是昨天两次。"},
                        {"role": "user", "content": "昨天次数不确定，记不清。"},
                    ]
                )
            elif kind == "other":
                evidence = [
                    {
                        "role": "user",
                        "content": f"朋友喜欢{topic}先列清单，他昨天做两次，我没有说自己的情况。",
                    }
                ]
            elif kind == "assistant":
                evidence = [
                    {
                        "role": "assistant",
                        "content": f"你可能喜欢{topic}先写一句，昨天大概尝试两次。",
                    }
                ]
            result.append(
                make(
                    "memory",
                    {"query": q, "evidence": evidence},
                    {"evidence_sufficient": "yes" if kind == "complete" else "no"},
                    f"r5-memory-suff-{split}-{i}-{kind}",
                    split,
                )
            )
    return result


def challenge():
    cases = []

    def add(profile, name, state, expected):
        cases.append(
            {"profile": profile, "name": "round5-" + name, "state": state, "expected": expected}
        )

    for i, (text, hist, risk, kind, need) in enumerate(
        [
            (
                "目前没有想伤害自己的念头、意图和准备，我想分享今天做饭的小事。",
                [],
                "low",
                "chat",
                "general",
            ),
            (
                "那阵冲动缓下来了，现在没有行动计划或准备，但我还是想有人陪着。",
                [{"role": "user", "content": "刚才我想自伤。"}],
                "elevated",
                "counseling",
                "professional",
            ),
            (
                "我现在安全，不再有伤害自己的想法。今天天气不错，我出去买了文具，想随便聊聊。",
                [
                    {"role": "user", "content": "我现在想伤害自己，不想活了。"},
                    {"role": "assistant", "content": "先联系可信任的人陪伴，获取支持。"},
                ],
                "elevated",
                "counseling",
                "professional",
            ),
            (
                "嘴上说不想死，但我已经准备好工具，今晚马上行动。",
                [],
                "urgent",
                "counseling",
                "professional",
            ),
            ("我已经采取自伤行动，现在需要立刻帮助。", [], "urgent", "counseling", "professional"),
            (
                "我想寻找心理咨询的求助渠道，目前没有迫近危险。",
                [],
                "low",
                "counseling",
                "professional",
            ),
            ("我不需要咨询资源，今天只是聊聊新买的食材。", [], "low", "chat", "general"),
            (
                "困扰已经持续一个月，完全无法正常工作，需要专业帮助。",
                [],
                "elevated",
                "counseling",
                "professional",
            ),
        ]
    ):
        add(
            "router",
            f"risk-{i}",
            {"message": text, "history": hist, "session_summary": ""},
            {"route": "chat", "conversation_kind": kind, "risk_band": risk, "support_need": need},
        )
    tasks = [
        ("把九张照片分好类", "只分了三张", "九张都分好了"),
        ("录完四分钟音频", "只录了半分钟", "四分钟已经录完"),
        ("把邀请函发出", "只写了称呼还没发", "邀请函已经发出"),
    ]
    for i, (title, partial, done) in enumerate(tasks):
        candidates = [
            {
                "goalTitle": "完成一件具体小事",
                "actionTitle": title,
                "goalId": 1,
                "version": 1,
                "actionId": "target",
            },
            {
                "goalTitle": "安排休息",
                "actionTitle": "闭眼休息三分钟",
                "goalId": 2,
                "version": 1,
                "actionId": "other",
            },
        ]
        for j, (text, outcome) in enumerate(
            [
                (f"我试了，{partial}，还没达到这步标准。", "partial"),
                (f"剩下的还没弄，{partial}，不是全完成。", "partial"),
                (f"{done}，是我今天本人做的。", "done"),
                (f"假设{done}了会如何？我今天并未做。", "none"),
                (f"朋友{done}了，我还没动。", "none"),
                (f"我打算明天{title}，现在只有计划。", "none"),
            ]
        ):
            add(
                "executor",
                f"progress-{i}-{j}",
                {"userExpression": text, "candidates": candidates},
                {
                    "goal_action": "action_0" if outcome != "none" else "none",
                    "goal_outcome": outcome,
                },
            )
    fact = "准备摄影展时，我自己的习惯是先选一张照片，而不是一下挑很多。"
    add(
        "memory",
        "write-preference",
        {"observation": fact},
        {"episodic": "no", "preference": "yes", "procedural": "no", "semantic": "yes"},
    )
    add(
        "memory",
        "write-quote",
        {"observation": "助手说可以先选照片，我没有采用，也没说自己喜欢。"},
        {"episodic": "no", "preference": "no", "procedural": "no", "semantic": "no"},
    )
    for pos in range(4):
        cs = [{"role": "assistant", "content": "你可以先选照片。"} for _ in range(4)]
        cs[pos] = {"role": "user", "content": fact}
        add(
            "memory",
            f"relevance-{pos}",
            {"query": "准备摄影展时我自己偏好怎么开始？", "candidates": cs},
            {f"relevance_{j}": "yes" if j == pos else "no" for j in range(4)},
        )
    for name, ev, label in [
        ("missing", [{"role": "user", "content": fact}], "no"),
        (
            "complete",
            [
                {"role": "user", "content": fact},
                {"role": "user", "content": "我昨天下午实际选照片三次。"},
            ],
            "yes",
        ),
        ("quoted", [{"role": "assistant", "content": fact + "昨天三次。"}], "no"),
    ]:
        add(
            "memory",
            "suff-" + name,
            {"query": "摄影展我偏好怎么开始，昨天下午实际做了几次？", "evidence": ev},
            {"evidence_sufficient": label},
        )
    return cases


def build(base, out):
    held = {
        hashlib.sha256(
            json.dumps(c["state"], sort_keys=True, ensure_ascii=False).encode()
        ).hexdigest()
        for c in challenge()
    }
    manifest = {}
    for role, gen in [("router", router), ("executor", executor), ("memory", memory)]:
        # Current Executor v4 has a deliberately smaller training replay.
        original = load_parts(base / role, role, allow_teacher=True)
        dest = out / role
        dest.mkdir(parents=True, exist_ok=True)
        counts = {}
        for split in SPLITS:
            replay = list(original[split])
            random.Random(511).shuffle(replay)
            if split == "train":
                replay = replay[:650]
            fresh = gen(split)
            rows = replay + fresh
            for r in rows:
                digest = hashlib.sha256(
                    json.dumps(r["state"], sort_keys=True, ensure_ascii=False).encode()
                ).hexdigest()
                if digest in held:
                    raise ValueError("challenge leaks into dataset")
            (dest / f"{split}.jsonl").write_text(
                "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows)
            )
            counts[split] = {"replay": len(replay), "newAuthored": len(fresh), "total": len(rows)}
        load_parts(dest, role, allow_teacher=True)
        manifest[role] = counts
    (out / "curriculum-manifest.json").write_text(json.dumps(manifest, indent=2))
    return manifest


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--out", type=Path, required=True)
    p.add_argument("--base", type=Path)
    p.add_argument("--challenge-only", action="store_true")
    a = p.parse_args()
    a.out.mkdir(parents=True, exist_ok=True)
    raw = "".join(json.dumps(c, ensure_ascii=False) + "\n" for c in challenge())
    (a.out / "challenge.jsonl").write_text(raw)
    print(
        json.dumps(
            {"challengeRows": len(challenge()), "sha256": hashlib.sha256(raw.encode()).hexdigest()}
        )
    )
    if not a.challenge_only:
        if not a.base:
            p.error("--base required")
        print(json.dumps(build(a.base, a.out)))


if __name__ == "__main__":
    main()
