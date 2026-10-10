"""Conservative progress pairs; API paraphrases go to TRAIN only as teacher drafts."""

import argparse
import asyncio
import json
import random
import re
from pathlib import Path

from psych_agent_runtime.config import Settings
from psych_agent_runtime.deepseek_client import DeepSeekClient

from .decision_data import load_parts
from .refine_decisions import ACTIONS, executor, make


async def generate(out):
    client = DeepSeekClient(Settings())
    rows = []
    # Fixed actor/action labels are specified before generation; no clinical data.
    for batch in range(12):
        specs = []
        for j in range(8):
            i = batch * 8 + j
            title, done, partial = ACTIONS[i % len(ACTIONS)]
            category = ["third_party", "imagined", "future", "assistant_quote"][i // 8 % 4]
            specs.append(
                {
                    "id": i,
                    "action": title,
                    "actualDone": done,
                    "actualPartial": partial,
                    "positiveOutcome": "done" if i % 2 else "partial",
                    "negativeCategory": category,
                }
            )
        path = out / f"teacher-batch-{batch}.json"
        if path.exists():
            generated = json.loads(path.read_text())
        else:
            generated = await client.chat_json(
                [
                    {
                        "role": "system",
                        "content": "生成中文目标进度训练用的虚构口语。输出JSON对象含cases数组。每个规格生成两句：positive是本人今天实际完成或实际做了一部分（严格按positiveOutcome）；negative是别人做了、仅想象、未来计划或助手引用，用户本人完全没做。negative必须明确说自己尚未行动，同时包含该negativeCategory的线索。保持小行动、数量、时长不变。口语自然，不套同一种句式，不添加临床或危险情节。每项只返回id、positive、negative。每句不超过80字。",
                    },
                    {"role": "user", "content": json.dumps(specs, ensure_ascii=False)},
                ],
                temperature=0.65,
                purpose="executor_training_paraphrases",
            )
            path.write_text(json.dumps(generated, ensure_ascii=False, indent=2))
        items = {item["id"]: item for item in generated["cases"]}
        if set(items) != {s["id"] for s in specs}:
            raise ValueError("teacher batch missing scenario IDs")
        for s in specs:
            item = items[s["id"]]
            if any(
                not isinstance(item[k], str) or not 5 <= len(item[k]) <= 120
                for k in ["positive", "negative"]
            ):
                raise ValueError("invalid generated utterance")
            for kind in ["positive", "negative"]:
                pos = s["id"] % 3
                options = ["写下今日感受", "查一下周末安排", "把房间通风两分钟"]
                options[pos] = s["action"]
                state = {
                    "userExpression": item[kind],
                    "candidates": [
                        {
                            "goalTitle": "尝试一件小事",
                            "actionTitle": t,
                            "goalId": 10 + j,
                            "version": 1,
                            "actionId": f"owned-{j}",
                        }
                        for j, t in enumerate(options)
                    ],
                }
                labels = (
                    {"goal_action": f"action_{pos}", "goal_outcome": s["positiveOutcome"]}
                    if kind == "positive"
                    else {"goal_action": "none", "goal_outcome": "none"}
                )
                row = make("executor", state, labels, f"teacher-v4-{s['id']}-{kind}", "train")
                row.update(
                    reviewed=False,
                    reviewedBy=None,
                    annotationStatus="teacher_draft",
                    source="deepseek_generated_scenario_pair",
                    teacherModel=client.model,
                )
                rows.append(row)
        print(
            json.dumps({"completedBatches": batch + 1, "trainingDraftRows": len(rows)}), flush=True
        )
    await client.close()
    (out / "teacher-train.jsonl").write_text(
        "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows)
    )


def authored(split):
    forms = {
        "train": [
            "别人说已经{done}，我本人尚未开始。",
            "我只在脑海里想象{done}，事实上完全没做。",
            "假如我{done}会怎样？我现在还未动手。",
            "这是助手的举例：{done}。我实际并没有做。",
            "我只是打算以后{title}，今天一点都没开始。",
            "我今天实际做了一部分：{partial}，其余尚未完成。",
            "我刚才亲自{done}，这一步已经完成。",
        ],
        "calibration": [
            "这件事{done}的是朋友，我还没有行动。",
            "我只是幻想自己{done}，并未实践。",
            "如果我{done}，可能会轻松吧，但这只是设想。",
            "助手描述{done}的样子，我没有执行。",
            "下次再{title}，当前只有计划。",
            "我已经尝试，实际{partial}，还需要继续。",
            "今天我确实{done}，现在做完了。",
        ],
        "validation": [
            "同事做到了{done}，并不是我，我尚未开始。",
            "我在想象{done}的情景，还没有真的做。",
            "假设自己{done}了，我实际上没有做过。",
            "书中说某人{done}，那不是我的行动。",
            "我准备未来{title}，今天尚未做。",
            "我刚刚动手了，{partial}，只做了一些。",
            "这次我实际上{done}，已经结束这一步。",
        ],
        "test": [
            "家人已经{done}了，我这边还完全没动。",
            "脑中模拟了{done}，现实中还没开始。",
            "只是讨论万一{done}会如何，尚未实践。",
            "之前举例说{done}，是例子，不是我的进度。",
            "明日想{title}，到现在都没有做。",
            "刚才我真的试了，{partial}，剩余部分还没做。",
            "我本人今天已经{done}，确实全部做好。",
        ],
    }
    rows = []
    for i, (title, done, partial) in enumerate(ACTIONS):
        for j, form in enumerate(forms[split]):
            for n in [2, 4]:
                pos = (i + j) % n
                options = [a[0] for a in ACTIONS if a[0] != title][:n]
                options[pos] = title
                cs = [
                    {
                        "goalTitle": "处理一个小目标",
                        "actionTitle": a,
                        "goalId": k + 1,
                        "version": 2,
                        "actionId": f"step-{k}",
                    }
                    for k, a in enumerate(options)
                ]
                outcome = "partial" if j == 5 else "done" if j == 6 else "none"
                labels = {
                    "goal_action": "none" if outcome == "none" else f"action_{pos}",
                    "goal_outcome": outcome,
                }
                rows.append(
                    make(
                        "executor",
                        {
                            "userExpression": form.format(title=title, done=done, partial=partial),
                            "candidates": cs,
                        },
                        labels,
                        f"v4-authored-{split}-{i}-{j}-{n}",
                        split,
                    )
                )
    return rows


def fresh_challenge():
    scenarios = [
        ("给三盆植物浇水", "三盆植物都浇好水了", "仅给第一盆浇了水"),
        ("完成十分钟拉伸", "整整十分钟拉伸已经做完", "拉伸只做了两分钟"),
        ("把六本书归位", "六本书都摆回书架", "只放回去两本书"),
        ("把预约邮件发出去", "预约邮件已经发出", "邮件草稿只写了一半，没发出去"),
    ]
    # Fresh evaluation is frozen before this generation/training run, flat wording.
    forms = [
        ("我刚刚{done}，这一步确实完成了。", "done"),
        ("今天本人已经开始，{partial}，剩下的还没做。", "partial"),
        ("朋友说他{done}，我没有做，是在讲他的事。", "none"),
        ("我幻想自己{done}的样子，现实里没有行动。", "none"),
        ("假如我{done}了会如何？只是提问，尚未执行。", "none"),
        ("助手的话是：{done}之后可以休息。这不是我已做过。", "none"),
        ("明天才打算{title}，现在还没开始。", "none"),
        ("刚才我尝试{title}，遇到障碍，卡在中间。", "blocked"),
    ]
    result = []
    for i, (title, done, partial) in enumerate(scenarios):
        for j, (form, outcome) in enumerate(forms):
            pos = (i + j) % 3
            options = ["准备早餐", "整理便签", "确认日程"]
            options[pos] = title
            cs = [
                {
                    "goalTitle": "日常安排",
                    "actionTitle": a,
                    "goalId": 20 + k,
                    "version": 0,
                    "actionId": f"fresh-{k}",
                }
                for k, a in enumerate(options)
            ]
            result.append(
                {
                    "profile": "executor",
                    "name": f"fresh-{i}-{j}",
                    "state": {
                        "userExpression": form.format(title=title, done=done, partial=partial),
                        "candidates": cs,
                    },
                    "expected": {
                        "goal_action": "none" if outcome == "none" else f"action_{pos}",
                        "goal_outcome": outcome,
                    },
                }
            )
    # A separate phrasing set for tool priority and sufficient-result termination.
    for i, (query, workflow, target, other) in enumerate(
        [
            (
                "我曾说过学习时偏好什么节奏，还记得吗？",
                "memory",
                "memory_search",
                "get_goal_context",
            ),
            (
                "请查一下我那个练习计划已经完成哪些步骤。",
                "strategy",
                "get_goal_context",
                "memory_search",
            ),
            ("想看看最近聊天的心理状况报告。", "dashboard", "get_dashboard", "memory_search"),
            (
                "检索一些情绪调节的知识，附资料出处。",
                "knowledge",
                "search_knowledge",
                "memory_search",
            ),
        ]
    ):
        for answered in [False, True]:
            value = {
                "memory_search": {
                    "status": "sufficient",
                    "evidence": [{"role": "user", "content": "我喜欢先学五分钟，再休息一会儿。"}],
                },
                "get_goal_context": {
                    "goals": [
                        {"title": "练习计划", "actions": [{"title": "练习一次", "status": "done"}]}
                    ]
                },
                "get_dashboard": {"summary": "最近三轮聊天出现紧张和放松两种感受。"},
                "search_knowledge": {
                    "hits": [{"text": "可以识别和记录情绪。", "source": "心理知识资料"}]
                },
            }[target]
            state = {
                "request": query,
                "workflow": workflow,
                "allowedTools": [other] if answered else [target, other],
                "remaining_calls": 1 if answered else 2,
                "observations": {target: json.dumps(value, ensure_ascii=False)} if answered else {},
            }
            result.append(
                {
                    "profile": "executor",
                    "name": f"fresh-tool-{i}-{answered}",
                    "state": state,
                    "expected": {"next_action": "finish" if answered else target},
                }
            )
    return result


def build(base, out, teacher):
    original = load_parts(base, "executor", allow_teacher=True)
    out.mkdir(parents=True, exist_ok=True)
    generated = [json.loads(s) for s in teacher.read_text().splitlines()]
    drafts, exclusions = [], []
    for item in generated:
        text = item["state"]["userExpression"]
        # Assigned scenario labels do not override what the generated sentence says.
        if item["expected"]["goal_outcome"] == "partial" and not re.search(
            r"只|一半|一部分|几个|还没|没.*完|二十秒|十秒|一分钟", text
        ):
            exclusions.append(
                {
                    "splitGroup": item["splitGroup"],
                    "text": text,
                    "reason": "partial scenario generated without partial-progress evidence",
                }
            )
        else:
            drafts.append(item)
    (out / "teacher-exclusions.json").write_text(
        json.dumps(exclusions, ensure_ascii=False, indent=2)
    )
    manifest = {}
    for split in ["train", "calibration", "validation", "test"]:
        replay = list(original[split])
        if split == "train":
            random.Random(401).shuffle(replay)
            replay = replay[:600]
        # Keep masked multi-tool transitions; remove v3 progress wrappers from train.
        extra = [r for r in executor(split) if "next_action" in r["expected"]]
        fresh = authored(split)
        rows = replay + extra + fresh + (drafts if split == "train" else [])
        (out / f"{split}.jsonl").write_text(
            "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows)
        )
        manifest[split] = {
            "replay": len(replay),
            "toolTransitions": len(extra),
            "authoredBoundary": len(fresh),
            "teacherDrafts": len(drafts) if split == "train" else 0,
            "total": len(rows),
        }
    load_parts(out, "executor", allow_teacher=True)
    (out / "curriculum-manifest.json").write_text(json.dumps(manifest, indent=2))
    return manifest


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--out", type=Path, required=True)
    p.add_argument("--generate", action="store_true")
    p.add_argument("--base", type=Path)
    p.add_argument("--teacher", type=Path)
    a = p.parse_args()
    a.out.mkdir(parents=True, exist_ok=True)
    if a.generate:
        asyncio.run(generate(a.out))
    elif a.base:
        print(json.dumps(build(a.base, a.out, a.teacher)))
    else:
        (a.out / "challenge-v4.jsonl").write_text(
            "".join(json.dumps(c, ensure_ascii=False) + "\n" for c in fresh_challenge())
        )
