"""Boundary-case curriculum with separate utterance families for each split.

Author-defined contract labels, not clinician-reviewed dialogues. The challenge
set is authored separately and must never be used as a training/selection split.
"""

import argparse
import hashlib
import json
import random
from pathlib import Path

from .build_domain_decisions import row
from .decision_data import load_parts

SPLITS = ("train", "calibration", "validation", "test")
ACTIONS = [
    ("写下一句核心观点", "观点已经写成完整的一句话", "观点只写了前几个字"),
    ("整理两条会议重点", "两条会议重点都整理出来", "会议重点只整理了一条"),
    ("练习三分钟开场", "开场练习满了三分钟", "开场仅练了二十秒"),
    ("给同事发送一条消息", "消息已经发给同事", "消息仅起草了一半，还没发送"),
    ("读完一页材料", "一页材料已经从头读到尾", "这页材料只读了第一段"),
    ("完成五分钟散步", "散步已经走满五分钟", "散步只走了一分钟"),
    ("写三个备忘要点", "备忘的三个要点都写齐", "备忘只写了一个要点"),
    ("关闭屏幕休息两分钟", "屏幕关掉并休息满两分钟", "关屏后才休息了十秒"),
]
TOPICS = ["准备例会", "整理课程", "安排休息", "表达自己的需要", "准备发言", "处理待办"]


def make(profile, state, labels, group, split):
    item = row(profile, state, labels, group)
    return {
        **item,
        "split": split,
        "source": "authored_boundary_contract_v3",
        "reviewedBy": "authored_contract_check_not_clinical",
    }


def executor(split):
    family = SPLITS.index(split)
    result = []
    # Different wording families, no synthetic timestamp used to disguise duplicates.
    progress = {
        "done": [
            "我刚才{done}，这件事做完了。",
            "今天我实际做到了：{done}。",
            "我已经{done}，这一小步确实结束了。",
            "刚刚我{done}，本次行动完成。",
        ],
        "partial": [
            "我已经动手，但{partial}，还没有全部做完。",
            "今天只推进了一部分：{partial}。",
            "我刚才试了一点，{partial}，这一步仍未结束。",
            "我已经开始做了，实际只有{partial}，不能算完成。",
        ],
        "blocked": [
            "我刚才尝试{title}，卡住了，无法继续。",
            "今天{title}时遇到了阻碍，暂时做不下去。",
            "我已经试着{title}，但被困难挡住了。",
            "刚才想继续{title}，现在卡在中间。",
        ],
        "rest": [
            "今天关于{title}，我选择先休息一下。",
            "这次{title}先放一放，我已经决定休息。",
            "我今天暂停这次{title}，先歇一会儿，目标还继续。",
            "今天这一步{title}先不做，实际选择是休息。",
        ],
        "future": [
            "明天我打算{title}，今天还没有做。",
            "我希望之后能{title}，只是计划。",
            "等周末再{title}，目前尚未行动。",
            "如果有时间，我会{title}，现在没开始。",
        ],
        "other": [
            "朋友已经{done}，我只是转述，我没有做。",
            "同事说他{done}，这不是我的行动。",
            "这是助手给的例子：{done}。我未执行。",
            "文章描述一个人{done}，与我的进度无关。",
        ],
    }
    for topic_i, topic in enumerate(TOPICS):
        for i, (title, done, partial) in enumerate(ACTIONS):
            alternatives = [a[0] for a in ACTIONS if a[0] != title]
            n = 2 + (i + topic_i) % 6
            options = [title, *alternatives[: n - 1]]
            random.Random(4100 + i + topic_i * 17 + family).shuffle(options)
            candidates = [
                {
                    "goalTitle": topic,
                    "actionTitle": action,
                    "goalId": j + 1,
                    "version": 2,
                    "actionId": f"owned-{j}",
                }
                for j, action in enumerate(options)
            ]
            for outcome, forms in progress.items():
                text = forms[family].format(title=title, done=done, partial=partial)
                # Distinct goal context is real task information, not a fake clock suffix.
                text = f"关于我的目标「{topic}」，" + text
                labels = (
                    {"goal_action": "none", "goal_outcome": "none"}
                    if outcome in {"future", "other"}
                    else {"goal_action": f"action_{options.index(title)}", "goal_outcome": outcome}
                )
                result.append(
                    make(
                        "executor",
                        {"userExpression": text, "candidates": candidates},
                        labels,
                        f"v3-progress-{split}-{topic_i}-{i}-{outcome}",
                        split,
                    )
                )
    requests = {
        "memory_search": [
            "之前聊{topic}时，我说过自己喜欢什么方式？",
            "能回忆一下我曾谈过的{topic}偏好吗？",
            "我上次关于{topic}的想法是什么，你还记得吗？",
            "以前我们聊{topic}，我当时具体怎么说的？",
        ],
        "get_goal_context": [
            "查看{topic}这个目标，我现在走到哪一步了？",
            "我的{topic}目标还剩下哪些小步骤？",
            "查一下{topic}的已完成行动和接下来步骤。",
            "{topic}目标的当前进度是什么，帮我看记录。",
        ],
        "get_dashboard": [
            "看最近几轮{topic}对话的心理概要。",
            "我的{topic}报告里有哪些趋势？",
            "打开关于{topic}的报告统计。",
            "这几次聊{topic}后的概况是什么？",
        ],
        "search_knowledge": [
            "解释{topic}时紧张的心理机制，要有资料出处。",
            "找一些{topic}相关心理知识和来源。",
            "有没有关于{topic}的可靠心理知识？",
            "我想了解{topic}相关原理，请检索材料。",
        ],
    }
    workflows = {
        "memory_search": "memory",
        "get_goal_context": "strategy",
        "get_dashboard": "dashboard",
        "search_knowledge": "knowledge",
    }
    permissions = {
        "memory": ["memory_search", "get_goal_context"],
        "strategy": ["get_goal_context", "search_knowledge", "memory_search"],
        "dashboard": ["get_dashboard"],
        "knowledge": ["search_knowledge", "memory_search"],
    }
    for t, forms in requests.items():
        workflow = workflows[t]
        for i, topic in enumerate(TOPICS):
            request = forms[family].format(topic=topic)
            for phase in ["initial", "distractor", "answered", "unavailable", "budget"]:
                available = list(permissions[workflow])
                obs = {}
                action = t
                if phase == "distractor" and t == "memory_search":
                    obs = {
                        "get_goal_context": json.dumps(
                            {
                                "goals": [
                                    {
                                        "title": topic,
                                        "actions": [{"title": "写一句话", "status": "planned"}],
                                    }
                                ]
                            },
                            ensure_ascii=False,
                        )
                    }
                    available.remove("get_goal_context")
                elif phase == "distractor":
                    obs = {"unrelated_context": "用户今天提到了天气，这不回答当前请求"}
                elif phase == "answered":
                    value = {
                        "memory_search": {
                            "evidence": [
                                {"role": "user", "content": f"关于{topic}，我喜欢先做最小的一步。"}
                            ],
                            "status": "sufficient",
                            "controller": "laya",
                        },
                        "get_goal_context": {
                            "goals": [
                                {
                                    "title": topic,
                                    "actions": [{"title": "写一句話", "status": "planned"}],
                                }
                            ]
                        },
                        "get_dashboard": {"summary": "有三次对话，最近谈到紧张和疲惫。"},
                        "search_knowledge": {
                            "hits": [{"text": "压力和任务评价有关。", "source": "心理知识资料"}]
                        },
                    }[t]
                    obs = {t: json.dumps(value, ensure_ascii=False)}
                    available.remove(t)
                    action = "finish"
                elif phase == "unavailable":
                    obs = {t: json.dumps({"unavailable": True})}
                    available.remove(t)
                    action = "finish"
                elif phase == "budget":
                    action = "finish"
                state = {
                    "request": request,
                    "workflow": workflow,
                    "allowedTools": available,
                    "remaining_calls": 0 if phase == "budget" else 2 if phase == "initial" else 1,
                    "observations": obs,
                }
                result.append(
                    make(
                        "executor",
                        state,
                        {"next_action": action},
                        f"v3-tools-{split}-{t}-{i}-{phase}",
                        split,
                    )
                )
    return result


def memory(split):
    family = SPLITS.index(split)
    result = []
    forms = [
        (
            "我平时更喜欢{fact}，这是我自己的偏好。",
            "我一向倾向于{fact}。",
            "对我来说，{fact}会更合适，我喜欢这样。",
            "说到习惯，我自己喜欢{fact}。",
        ),
        (
            "我刚才实际{event}了。",
            "今天我亲自{event}了，这是刚发生的事。",
            "我已经{event}了，确实做过。",
            "昨天我实际做的事情是{event}。",
        ),
        (
            "我实际使用的方法是{method}。",
            "我平时的做法是{method}。",
            "我已经在用这个方法：{method}。",
            "我处理事情时按这个过程做：{method}。",
        ),
        (
            "我目前{stable}。",
            "关于我自己的情况：{stable}。",
            "我现在的实际安排是{stable}。",
            "这是我的个人事实：{stable}。",
        ),
        (
            "别人喜欢{fact}，我只是转述，没说我的偏好。",
            "助手建议{method}，我尚未采用。",
            "这里是虚构例子：某人{event}，不是我的经历。",
            "文章里的人{stable}，并不是我的个人信息。",
        ),
    ]
    facts = [
        "一次只做一个小步骤",
        "先说想法再写下来",
        "用纸笔整理事情",
        "安静独处一会儿",
        "把任务缩成一行",
        "先留出休息时间",
        "做简短的列表",
        "与熟悉的人交谈",
    ]
    events = [
        "写好会议标题",
        "散步走了五分钟",
        "读完一页书",
        "给朋友发了邀请",
        "试着练了开场",
        "把两件待办记下来",
        "去参加课程",
        "关屏休息一分钟",
    ]
    methods = [
        "先列主题，再选最容易的一项",
        "先写一句想法，再加一个例子",
        "先关掉通知，再做五分钟",
        "先说明感受，再提出需要",
        "先读一段，再记一句",
        "先定小步骤，再留下感受",
        "先休息片刻，再开始做事",
        "先问清要求，再列任务",
    ]
    stable = [
        "每周三参加读书课",
        "正在准备月底的答辩",
        "在异地工作",
        "需要轮班上班",
        "住得离学校很近",
        "正在学习新的课程",
        "周末要照顾家人",
        "工作需要经常发言",
    ]
    labels = [
        {"episodic": "no", "preference": "yes", "procedural": "no", "semantic": "yes"},
        {"episodic": "yes", "preference": "no", "procedural": "no", "semantic": "yes"},
        {"episodic": "no", "preference": "no", "procedural": "yes", "semantic": "yes"},
        {"episodic": "no", "preference": "no", "procedural": "no", "semantic": "yes"},
        {"episodic": "no", "preference": "no", "procedural": "no", "semantic": "no"},
    ]
    for k in range(5):
        for i in range(8):
            text = forms[k][family].format(
                fact=facts[i], event=events[i], method=methods[i], stable=stable[i]
            )
            result.append(
                make("memory", {"observation": text}, labels[k], f"v3-write-{split}-{k}-{i}", split)
            )
    # Query expressions and evidence sentence families stay split-specific.
    queries = [
        "我关于{topic}的偏好是什么？",
        "上次我说{topic}更愿意怎么做？",
        "你能回忆我在{topic}时喜欢的方式吗？",
        "以前提到{topic}，我自己的倾向是什么？",
    ]
    for topic_i, topic in enumerate(TOPICS):
        for pos in range(8):
            for supported in [True, False]:
                query = queries[family].format(topic=topic)
                positive = forms[0][family].format(fact=f"{topic}先从一项小事开始")
                neg = f"朋友的{topic}方式是先做一项小事，这不是我的偏好。"
                candidates = [
                    {
                        "role": "user",
                        "content": positive
                        if j == pos and supported
                        else neg
                        if j % 2
                        else f"我刚才读完一页书，没谈到{topic}偏好。",
                        "observedAt": "2026-10-10T00:00:00",
                        "source": f"boundary-{topic_i}-{j}",
                    }
                    for j in range(8)
                ]
                lab = {
                    f"relevance_{j}": "yes" if j == pos and supported else "no" for j in range(8)
                }
                lab["temporal_need"] = "no"
                result.append(
                    make(
                        "memory",
                        {"query": query, "candidates": candidates},
                        lab,
                        f"v3-relevance-{split}-{topic_i}-{pos}-{supported}",
                        split,
                    )
                )
                if pos == 0:
                    evidence = [{"role": "user", "content": positive if supported else neg}]
                    result.append(
                        make(
                            "memory",
                            {"query": query, "evidence": evidence},
                            {"evidence_sufficient": "yes" if supported else "no"},
                            f"v3-sufficient-{split}-{topic_i}-{supported}",
                            split,
                        )
                    )
        compound = query + "另外，这种方式我昨天实际用过几次？"
        result.append(
            make(
                "memory",
                {"query": compound, "evidence": [{"role": "user", "content": positive}]},
                {"evidence_sufficient": "no"},
                f"v3-incomplete-{split}-{topic_i}",
                split,
            )
        )
    return result


def build(base, out, replay=900):
    manifest = {}
    for profile, generator in [("executor", executor), ("memory", memory)]:
        original = load_parts(base / profile, profile, allow_teacher=True)
        dest = out / profile
        dest.mkdir(parents=True, exist_ok=True)
        counts = {}
        for split in SPLITS:
            old = list(original[split])
            if split == "train":
                random.Random(691).shuffle(old)
                old = old[:replay]
            fresh = generator(split)
            rows = old + fresh
            (dest / f"{split}.jsonl").write_text(
                "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows)
            )
            counts[split] = {"replay": len(old), "new": len(fresh), "total": len(rows)}
        load_parts(dest, profile, allow_teacher=True)
        manifest[profile] = counts
    (out / "curriculum-manifest.json").write_text(json.dumps(manifest, indent=2))
    return manifest


def challenge():
    """Frozen cross-topic diagnostics, distinct from every training/eval split."""
    cases = []

    def add(profile, name, state, expected):
        cases.append({"profile": profile, "name": name, "state": state, "expected": expected})

    for i, (query, goal, target, false_tool) in enumerate(
        [
            (
                "能记起我之前说过，遇到分歧时愿意用什么方式沟通吗？",
                "练习沟通",
                "memory_search",
                "get_goal_context",
            ),
            (
                "我上回提的学习习惯是什么，别替我猜。",
                "学习习惯",
                "memory_search",
                "get_goal_context",
            ),
            ("帮我看下读书目标还剩哪些没做。", "读书", "get_goal_context", "memory_search"),
            (
                "我要看看过去几次聊天的整体心理状态概要。",
                "心理概要",
                "get_dashboard",
                "get_goal_context",
            ),
            ("找一些解释拖延机制的心理知识，附来源。", "拖延", "search_knowledge", "memory_search"),
        ]
    ):
        workflow = {
            "memory_search": "memory",
            "get_goal_context": "strategy",
            "get_dashboard": "dashboard",
            "search_knowledge": "knowledge",
        }[target]
        for phase in ["initial", "distractor", "answered"]:
            state = {
                "request": query,
                "workflow": workflow,
                "remaining_calls": 2 if phase == "initial" else 1,
                "allowedTools": [target, false_tool],
                "observations": {},
            }
            expected = target
            if phase == "distractor":
                state["allowedTools"].remove(false_tool)
                state["observations"][false_tool] = json.dumps(
                    {
                        "goals": [
                            {
                                "title": goal,
                                "actions": [{"title": "列一条待办", "status": "planned"}],
                            }
                        ]
                    },
                    ensure_ascii=False,
                )
            elif phase == "answered":
                value = {
                    "memory_search": {
                        "evidence": [
                            {"role": "user", "content": "我习惯先把分歧写下来，再找熟悉的人聊。"}
                        ],
                        "status": "sufficient",
                    },
                    "get_goal_context": {
                        "goals": [
                            {"title": goal, "actions": [{"title": "读一章", "status": "planned"}]}
                        ]
                    },
                    "get_dashboard": {"summary": "最近三次对话谈到压力与期待。"},
                    "search_knowledge": {
                        "hits": [{"text": "拖延与任务评价和情绪调节有关", "source": "知识材料"}]
                    },
                }[target]
                state["allowedTools"].remove(target)
                state["observations"][target] = json.dumps(value, ensure_ascii=False)
                expected = "finish"
            add("executor", f"tool-{i}-{phase}", state, {"next_action": expected})
    scenarios = [
        ("我刚刚把那封申请信写到一半，已经开始，但还差结尾。", "写完申请信", "partial"),
        ("今天已经把申请信从头到尾写好了。", "写完申请信", "done"),
        ("明天再写申请信，今天尚未动笔。", "写完申请信", "none"),
        ("朋友已经写好申请信，我这边还没开始。", "写完申请信", "none"),
        ("我刚才把房间的书桌收拾了一角，其他地方没弄好。", "整理整张书桌", "partial"),
        ("整张书桌今天已经整理干净了。", "整理整张书桌", "done"),
        ("今天我尝试整理书桌，但不知道东西该放哪，卡住了。", "整理整张书桌", "blocked"),
        ("这次整理书桌我决定先休息，之后会继续。", "整理整张书桌", "rest"),
        ("刚才我只背会了两个单词，原定的十个还没背完。", "记住十个单词", "partial"),
        ("今天十个单词我已经全记住了。", "记住十个单词", "done"),
        ("我只是在想象自己背完十个单词，还没有实际背。", "记住十个单词", "none"),
        ("助手说我应该背十个单词，我还未行动。", "记住十个单词", "none"),
        ("我刚刚做了三次呼吸练习，原定五次，确实只做了部分。", "做五次呼吸练习", "partial"),
        ("五次呼吸练习我今天已经实际做完了。", "做五次呼吸练习", "done"),
    ]
    for i, (text, title, outcome) in enumerate(scenarios):
        idx = i % 3
        candidates = [
            {
                "goalTitle": "安排一个小目标",
                "actionTitle": t,
                "goalId": 100 + j,
                "version": 4,
                "actionId": f"challenge-{j}",
            }
            for j, t in enumerate(["把当天感受记在本子上", "预约下周的讨论", "读一段材料"])
        ]
        candidates[idx]["actionTitle"] = title
        add(
            "executor",
            f"progress-{i}",
            {"userExpression": text, "candidates": candidates},
            {
                "goal_action": "none" if outcome == "none" else f"action_{idx}",
                "goal_outcome": outcome,
            },
        )
    classifications = [
        ("我不太爱在人多的地方讨论，更愿意找一个安静角落。", ("no", "yes", "no", "yes")),
        ("我自己做选择时更愿意留一点思考时间。", ("no", "yes", "no", "yes")),
        ("我喜欢听别人先把话说完，然后再表达自己。", ("no", "yes", "no", "yes")),
        ("今天我真的去了一趟图书馆，借了两本书。", ("yes", "no", "no", "yes")),
        ("刚才我和姐姐通了十分钟电话。", ("yes", "no", "no", "yes")),
        ("我用的办法是先找关键词，再用自己的话复述。", ("no", "no", "yes", "yes")),
        ("每次我先写下分歧，然后约对方讨论，我一直这样处理。", ("no", "no", "yes", "yes")),
        ("我在外地上学，每周末会给家人打电话。", ("no", "no", "no", "yes")),
        ("朋友说他更爱独处，我只是转述，并没说我喜欢什么。", ("no", "no", "no", "no")),
        ("那是小说里的经历：主角去借书，我本人没有去。", ("no", "no", "no", "no")),
        ("助手说可以先练呼吸再发言，我还没用这个办法。", ("no", "no", "no", "no")),
    ]
    for i, (text, labels) in enumerate(classifications):
        add(
            "memory",
            f"write-{i}",
            {"observation": text},
            dict(zip(["episodic", "preference", "procedural", "semantic"], labels)),
        )
    query = "我遇到意见不一致时，自己更喜欢如何沟通？"
    positive = "遇到意见不一致，我更喜欢先各自把看法写下来，然后约时间聊。"
    negative = "朋友遇到分歧时喜欢马上辩论，我只是提到他的习惯。"
    for pos in range(8):
        cs = [
            {
                "role": "user",
                "content": positive
                if j == pos
                else negative
                if j % 2
                else "我昨晚借了两本书，没有提及沟通偏好。",
            }
            for j in range(8)
        ]
        add(
            "memory",
            f"relevance-{pos}",
            {"query": query, "candidates": cs},
            {f"relevance_{j}": "yes" if j == pos else "no" for j in range(8)},
        )
    for i, (q, ev, label) in enumerate(
        [
            (query, positive, "yes"),
            (query, negative, "no"),
            (query + "昨天实际用了几次？", positive, "no"),
            ("我昨晚借了几本书？", "我昨晚到图书馆借了两本书。", "yes"),
            ("我昨天实际做了多少次练习？", "我明天计划做三次练习，现在没开始。", "no"),
        ]
    ):
        add(
            "memory",
            f"sufficient-{i}",
            {"query": q, "evidence": [{"role": "user", "content": ev}]},
            {"evidence_sufficient": label},
        )
    return cases


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--base", type=Path)
    p.add_argument("--out", type=Path, required=True)
    p.add_argument("--challenge-only", action="store_true")
    a = p.parse_args()
    a.out.mkdir(parents=True, exist_ok=True)
    cases = challenge()
    raw = "".join(json.dumps(c, ensure_ascii=False) + "\n" for c in cases)
    (a.out / "challenge.jsonl").write_text(raw)
    print(
        json.dumps(
            {
                "challengeRows": len(cases),
                "challengeSha256": hashlib.sha256(raw.encode()).hexdigest(),
            }
        )
    )
    if not a.challenge_only:
        if not a.base:
            p.error("--base required for curriculum")
        print(json.dumps(build(a.base, a.out)))
