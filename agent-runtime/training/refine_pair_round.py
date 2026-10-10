"""Versioned pair-scoring curriculum and risk-context replay; no teacher API calls.

Old held-out groups retain their splits. Derived pair labels retain their review
status; synthetic checks are project-policy checks, not clinical ground truth.
"""

import argparse
import hashlib
import json
import random
from collections import Counter
from pathlib import Path

from psych_agent_runtime.decision_contract import MEMORY_PAIR_CONTRACT

from .decision_data import load_parts, prepare_row
from .refine_decisions import make

SPLITS = ("train", "calibration", "validation", "test")


def identity(state):
    return hashlib.sha256(
        json.dumps(state, ensure_ascii=False, sort_keys=True).encode()
    ).hexdigest()


def pair_rows(row):
    result = []
    base = {**row, "contractVersion": MEMORY_PAIR_CONTRACT}
    other = {t: v for t, v in row["expected"].items() if not t.startswith("relevance_")}
    if other:
        result.append({**base, "expected": other})
    for task, label in row["expected"].items():
        if not task.startswith("relevance_"):
            continue
        candidate = row["state"]["candidates"][int(task.split("_")[1])]
        role = candidate.get("role")
        if role is None:
            role = "assistant" if candidate["content"].startswith("助手建议：") else "user"
        if (
            row.get("source") == "public_fact_query_with_counterfactual_sources"
            and role == "user"
            and candidate["content"].startswith("这是朋友说的：")
        ):
            # This counterfactual also denies the user's own situation. Its original
            # 'no' may discard a useful premise correction, so exclude the draft pair.
            continue
        state = {
            "query": row["state"]["query"],
            "candidate": {"role": role, "content": candidate["content"]},
        }
        result.append({**base, "state": state, "expected": {"relevance": label}})
    return result


def pair_fixtures(split):
    """Distinct fact values and wording families; all examples are synthetic."""
    family = SPLITS.index(split)
    out = []
    days = ["星期一", "星期二", "星期三", "星期四", "星期五", "星期六", "星期日"]
    activities = ["游泳", "画画", "跳舞", "读书", "骑车", "慢跑", "做瑜伽", "弹琴"]
    templates = [
        "我每周{day}去{activity}。",
        "{activity}安排在每周{day}，这是我固定做的事。",
        "我习惯每周{day}参加{activity}。",
        "每到{day}，我就会去{activity}。",
    ]
    for i, activity in enumerate(activities):
        for j, day in enumerate(days):
            query = f"我每周哪天{activity}？"
            fact = templates[family].format(day=day, activity=activity)
            unrelated = templates[family].format(day=day, activity=activities[(i + 1) % 8])
            for role, content, label in [
                ("user", fact, "yes"),
                ("assistant", fact, "no"),
                ("user", unrelated, "no"),
                ("user", "朋友说：" + fact + "这是朋友的固定安排。", "no"),
            ]:
                r = make(
                    "memory",
                    {"observation": "placeholder"},
                    {"semantic": "no"},
                    f"pair-fixture-{split}-{i}-{j}",
                    split,
                )
                r.update(
                    contractVersion=MEMORY_PAIR_CONTRACT,
                    state={"query": query, "candidate": {"role": role, "content": content}},
                    expected={"relevance": label},
                    source="pair_source_policy_fixture",
                    validationScope="synthetic_pair_policy_not_clinical",
                )
                out.append(r)
    # English is explicit course coverage, not a translated clinical dataset.
    names = [
        ["ceramics", "running", "singing"],
        ["painting", "swimming", "cycling"],
        ["chess", "dancing", "gardening"],
        ["yoga", "drawing", "hiking"],
    ][family]
    for activity in names:
        for hours in [1, 2, 3, 4, 5, 6, 7, 8]:
            query = f"How many hours do I spend on {activity} each week?"
            fact = f"I spend {hours} hours on {activity} every week."
            for role, text, label in [
                ("user", fact, "yes"),
                ("assistant", fact, "no"),
                ("user", "My friend says: " + fact + " These are my friend's words.", "no"),
            ]:
                r = make(
                    "memory",
                    {"observation": "placeholder"},
                    {"semantic": "no"},
                    f"pair-en-{split}-{activity}-{hours}",
                    split,
                )
                r.update(
                    contractVersion=MEMORY_PAIR_CONTRACT,
                    state={"query": query, "candidate": {"role": role, "content": text}},
                    expected={"relevance": label},
                    source="pair_english_policy_fixture",
                )
                out.append(r)
    return out


def risk_fixtures(split):
    family = SPLITS.index(split)
    recent = [
        "刚才我有想伤害自己的冲动。",
        "今天早些时候我说过不想活了。",
        "前一会儿我想伤害自己，向你求助过。",
        "刚刚我表达过自伤想法。",
    ]
    current = [
        "现在那种念头减弱了，没有准备或计划，我还是想有人陪我说说。",
        "我暂时没有行动打算，也没有准备东西，但还希望获得支持。",
        "此刻没有实施计划，冲动缓下来了，先听听我好吗？",
        "现在没有准备行动，念头暂时少了，我仍需要陪伴。",
    ]
    out = []
    for i in range(24):
        topic = ["作业", "人际关系", "工作", "家庭生活", "考试", "面试"][i % 6]
        # Different context and wording families per split, no timestamps as uniqueness padding.
        expression = current[family] + f"今天让我难受的事情与{topic}有关。"
        for history, risk, support in [
            ([{"role": "user", "content": recent[family]}], "elevated", "professional"),
            ([], "low", "general"),
            (
                [
                    {
                        "role": "user",
                        "content": "朋友曾说："
                        + recent[family]
                        + "这是朋友的经历，我没有这些想法。",
                    }
                ],
                "low",
                "general",
            ),
        ]:
            if i >= 6:  # Only unique factual contexts, never inflate via duplicate rows.
                continue
            state = {"message": expression, "history": history, "session_summary": ""}
            r = make(
                "router",
                state,
                {
                    "route": "chat",
                    "conversation_kind": "counseling",
                    "risk_band": risk,
                    "support_need": support,
                },
                f"recent-risk-{split}-{i}-{risk}-{identity(state)[:8]}",
                split,
            )
            r["source"] = "recent_risk_context_policy_fixture"
            out.append(r)
    # Broaden recent risk wording within training only; held-out families remain untouched.
    if split == "train":
        for before in [
            "我刚才想自伤。",
            "我刚刚说不想活了。",
            "我今晚曾有伤害自己的念头。",
            "今天早些时候我觉得活不下去。",
            "之前几分钟我想结束生命。",
        ]:
            for now in [
                "此刻我没有行动计划，冲动少了一些，想继续说说。",
                "目前没准备伤害自己，但希望有人陪我。",
                "我现在安全，暂时不想采取行动，还需要支持。",
                "那些念头缓了一点，没有准备或计划，想倾诉。",
            ]:
                state = {
                    "message": now,
                    "history": [{"role": "user", "content": before}],
                    "session_summary": "",
                }
                r = make(
                    "router",
                    state,
                    {
                        "route": "chat",
                        "conversation_kind": "counseling",
                        "risk_band": "elevated",
                        "support_need": "professional",
                    },
                    "recent-risk-train-" + identity(state),
                    split,
                )
                r["source"] = "recent_risk_context_policy_fixture"
                out.append(r)
    return out


def ownership_fixtures(split):
    family = SPLITS.index(split)
    owner = ["朋友", "同事", "家人", "室友"][family]
    out = []
    for activity in ["画画", "跳舞", "骑车", "读书", "弹琴", "写日记", "练字", "烘焙"]:
        for day in ["星期一", "星期二", "星期三", "星期四", "星期五", "星期六", "星期日"]:
            query = f"我每周哪天{activity}？"
            for text, label in [
                (f"{owner}每周{day}{activity}，这是对方的习惯。", "no"),
                (f"{owner}每周{day}{activity}，我只是提到对方的安排。", "no"),
                (f"{owner}每周{day}{activity}，我在记录对方的经历。", "no"),
                (f"我和{owner}都每周{day}{activity}，这是我的固定安排。", "yes"),
            ]:
                r = make(
                    "memory",
                    {"observation": "placeholder"},
                    {"semantic": "no"},
                    f"pair-ownership-{split}-{activity}-{day}",
                    split,
                )
                r.update(
                    contractVersion=MEMORY_PAIR_CONTRACT,
                    state={"query": query, "candidate": {"role": "user", "content": text}},
                    expected={"relevance": label},
                    source="pair_third_person_policy_fixture",
                )
                out.append(r)
    return out


def denial_fixtures(split):
    family = SPLITS.index(split)
    out = []
    for activity in ["画画", "跳舞", "骑车", "读书", "弹琴", "写日记", "练字", "烘焙"]:
        question = [
            "我本周有{}吗？",
            "我这周是否实际{}了？",
            "这个星期我做过{}吗？",
            "本周我有没有进行{}？",
        ][family].format(activity)
        fact = [
            "我本周没有{}。",
            "这周我实际没有{}。",
            "这个星期我没做过{}。",
            "本周我没有进行{}。",
        ][family].format(activity)
        for role, text, label in [
            ("user", fact, "yes"),
            ("assistant", fact, "no"),
            ("user", "朋友说：" + fact, "no"),
        ]:
            r = make(
                "memory",
                {"observation": "placeholder"},
                {"semantic": "no"},
                f"owned-denial-{split}-{activity}",
                split,
            )
            r.update(
                contractVersion=MEMORY_PAIR_CONTRACT,
                state={"query": question, "candidate": {"role": role, "content": text}},
                expected={"relevance": label},
                source="pair_owned_denial_policy_fixture",
            )
            out.append(r)
    return out


def resource_fixtures(split):
    family = SPLITS.index(split)
    safe_help = [
        "目前我安全，想了解可以预约的心理咨询机构。",
        "现在没有危险，但我需要找到合适的心理咨询服务。",
        "我希望联系专业心理咨询师，目前并无紧急情况。",
        "我想获取心理咨询预约渠道，眼下是安全的。",
    ][family]
    decline = [
        "不用提供心理咨询服务，我只是想分享今天做的{}。",
        "我现在不想找咨询师，只想轻松聊聊今天的{}。",
        "暂时不需要专业求助，我只是来讲讲今天的{}。",
        "先不找心理咨询服务，今天我只想分享{}。",
    ][family]
    out = []
    for topic in ["烤面包", "手工", "晚饭", "咖啡", "冰淇淋", "蛋糕"]:
        for message, kind, support in [
            (safe_help + f"也想说说最近准备{topic}时的心情。", "counseling", "professional"),
            (decline.format(topic), "chat", "general"),
        ]:
            state = {"message": message, "history": [], "session_summary": ""}
            r = make(
                "router",
                state,
                {
                    "route": "chat",
                    "conversation_kind": kind,
                    "risk_band": "low",
                    "support_need": support,
                },
                "resource-negation-" + split + "-" + identity(state),
                split,
            )
            r["source"] = "professional_request_and_decline_policy_fixture"
            out.append(r)
    return out


def build(source, out, pilot=False, legacy_source=None):
    if out.exists():
        raise ValueError("refuse to overwrite an existing curriculum")
    rng = random.Random(711)
    manifest = {}
    for role in ["memory", "router"]:
        parts = {
            s: [json.loads(l) for l in (source / role / f"{s}.jsonl").read_text().splitlines() if l]
            for s in SPLITS
        }
        if role == "memory":
            parts = {
                s: [p for r in rows for p in pair_rows(r)]
                + pair_fixtures(s)
                + ownership_fixtures(s)
                + denial_fixtures(s)
                for s, rows in parts.items()
            }
            train = parts["train"]
            pair = [r for r in train if "relevance" in r["expected"]]
            other = [r for r in train if "relevance" not in r["expected"]]
            positives = [r for r in pair if r["expected"]["relevance"] == "yes"]
            negatives = [r for r in pair if r["expected"]["relevance"] == "no"]
            for rows in [positives, negatives, other]:
                rng.shuffle(rows)
            count = 128 if pilot else 800
            selected = positives[:count] + negatives[:count]
            # Stratify other tasks: retain independent typing, sufficiency and time coverage.
            for category in ["observation", "evidence", "candidates"]:
                selected += [r for r in other if category in r["state"]][: 96 if pilot else 240]
            selected += [
                r
                for r in train
                if r.get("source")
                in {
                    "pair_source_policy_fixture",
                    "pair_english_policy_fixture",
                    "pair_third_person_policy_fixture",
                    "pair_owned_denial_policy_fixture",
                }
            ]
            replay = [
                r
                for r in pair
                if r.get("reviewed") is True and not str(r.get("source")).startswith("pair_")
            ]
            rng.shuffle(replay)
            selected += replay[: 256 if pilot else 1000]
            parts["train"] = selected
            contract = MEMORY_PAIR_CONTRACT
        else:
            train = parts["train"]
            selected = []
            for risk, count in [
                ("low", 192 if pilot else 900),
                ("elevated", 128 if pilot else 450),
                ("urgent", 96 if pilot else 200),
            ]:
                subset = [r for r in train if r["expected"].get("risk_band") == risk]
                rng.shuffle(subset)
                selected += subset[:count]
            # Keep route coverage as well as balanced risk/support coverage.
            for route in ["memory", "dashboard", "strategy", "knowledge", "clarify"]:
                selected += [r for r in train if r["expected"].get("route") == route][
                    : 16 if pilot else 60
                ]
            selected += [
                r
                for r in train
                if r.get("reviewed") is True and r["expected"].get("support_need") == "professional"
            ][:100]
            selected += [r for r in train if r.get("reviewed") is True][: 180 if pilot else 400]
            parts["train"] = selected
            for split in SPLITS:
                parts[split] += risk_fixtures(split) + resource_fixtures(split)
            contract = None
        if legacy_source:
            replay = [
                json.loads(line)
                for line in (legacy_source / role / "train.jsonl").read_text().splitlines()
                if line
            ]
            replay = [r for r in replay if r.get("reviewed") is True]
            if role == "memory":
                replay = [
                    p for r in replay for p in pair_rows(r) if "relevance" not in p["expected"]
                ]
            parts["train"] += replay
        # Avoid projected pair duplicates in held-outs and train without moving a held-out
        # example into train. Whole source groups shared across splits are still rejected.
        seen, clean = set(), {}
        for split in ["calibration", "validation", "test", "train"]:
            clean[split] = []
            for row in parts[split]:
                prepared = prepare_row(row, role, True, contract)
                key = identity(prepared["state"])
                if key in seen:
                    continue
                seen.add(key)
                clean[split].append(prepared)
        target = out / role
        target.mkdir(parents=True)
        for split in SPLITS:
            rng.shuffle(clean[split])
            (target / f"{split}.jsonl").write_text(
                "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in clean[split])
            )
        load_parts(target, role, True, contract)
        manifest[role] = {
            "trainingScope": "pair_representation_and_risk_context_replay_with_source_group_holdouts",
            "contract": contract or "psych-router-v2",
            "pilot": pilot,
            "rows": {s: len(clean[s]) for s in SPLITS},
            "trainSources": dict(Counter(r.get("source") for r in clean["train"])),
            "riskLabels": dict(Counter(r["expected"].get("risk_band") for r in clean["train"]))
            if role == "router"
            else None,
            "review": "Teacher-derived train labels remain drafts. Synthetic held-outs are project policy only.",
            "ambiguityPolicy": "Exclude derived friend counterfactuals that explicitly deny the user's own fact; include owned denials as useful evidence.",
        }
    (out / "curriculum-manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2)
    )
    return manifest


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--source", type=Path, required=True)
    p.add_argument("--out", type=Path, required=True)
    p.add_argument("--pilot", action="store_true")
    p.add_argument("--legacy-source", type=Path, help="Read only the old training split")
    a = p.parse_args()
    print(
        json.dumps(build(a.source, a.out, a.pilot, a.legacy_source), ensure_ascii=False, indent=2)
    )
