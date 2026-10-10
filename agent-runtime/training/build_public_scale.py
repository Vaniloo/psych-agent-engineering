"""Mix public training drafts with reviewed project replay, without relabeling holdouts."""

import argparse
import json
import random
from collections import Counter
from pathlib import Path

from .decision_data import load_parts
from .refine_decisions import make
from .scale_public import digest


def draft(role, state, expected, item, source):
    row = make(role, state, expected, "public-r6-" + item["conversationId"], "train")
    row.update(
        reviewed=False,
        reviewedBy=None,
        annotationStatus="teacher_draft",
        source=source,
        sourceDataset=item["source"],
        sourceCaseId=item["caseId"],
        teacherModel=item["teacher"],
    )
    return row


def memory_rows(items):
    writes, relevance, sufficient = [], [], []
    for index, item in enumerate(items):
        writes.append(
            draft(
                "memory",
                {"observation": item["state"]["message"]},
                item["memory"],
                item,
                "public_user_observation_teacher",
            )
        )
        query, fact = item["memoryQuery"], item["factSpan"]
        if not query or not fact or item["memory"]["semantic"] != "yes":
            continue
        # These are source-derived counterfactuals, not raw conversation turns.
        negative = "这是朋友说的：" + fact + "；我自己不是这种情况。"
        cs = [
            {"role": "assistant", "content": fact},
            {"role": "user", "content": negative},
            {"role": "user", "content": fact},
        ]
        random.Random(index + 63).shuffle(cs)
        labels = {
            f"relevance_{j}": "yes" if c["role"] == "user" and c["content"] == fact else "no"
            for j, c in enumerate(cs)
        }
        labels["temporal_need"] = (
            "yes"
            if any(
                t in query
                for t in ["昨天", "今天", "之前", "后来", "什么时候", "几号", "最近", "当时"]
            )
            else "no"
        )
        relevance.append(
            draft(
                "memory",
                {"query": query, "candidates": cs},
                labels,
                item,
                "public_fact_query_with_counterfactual_sources",
            )
        )
        # Positive and absence-of-owned-evidence pairs share a source group.
        for role, label in [("user", "yes"), ("assistant", "no")]:
            sufficient.append(
                draft(
                    "memory",
                    {"query": query, "evidence": [{"role": role, "content": fact}]},
                    {"evidence_sufficient": label},
                    item,
                    "public_fact_query_owned_evidence_pair",
                )
            )
        # Need to answer BOTH parts, including a new field not in the quotation.
        if not any(c.isdigit() for c in fact) and not any(
            t in fact for t in ["分钟", "小时", "几次", "一次", "两次", "三次", "四次"]
        ):
            sufficient.append(
                draft(
                    "memory",
                    {
                        "query": query + "当时这件事具体持续了几分钟？",
                        "evidence": [{"role": "user", "content": fact}],
                    },
                    {"evidence_sufficient": "no"},
                    item,
                    "public_fact_query_missing_duration_counterfactual",
                )
            )
    # Even coverage, with real observations kept alongside retrieval tasks.
    result = []
    rng = random.Random(634)
    for collection in [writes, relevance, sufficient]:
        rng.shuffle(collection)
    positions = [0, 0, 0]
    schedule = [0, 0, 1, 2]
    collections = [writes, relevance, sufficient]
    while any(p < len(rows) for p, rows in zip(positions, collections, strict=True)):
        for k in schedule:
            if positions[k] < len(collections[k]):
                result.append(collections[k][positions[k]])
                positions[k] += 1
    return result


def executor_auxiliary(progress):
    goals = {}
    for row in progress:
        for candidate in row["state"]["candidates"]:
            if candidate["goalTitle"] != "安排日常生活":
                goals[candidate["goalTitle"]] = row
    tools, creation = [], []
    specs = [
        (
            "memory_search",
            "memory",
            "你记得我以前提过「{}」的哪些感受吗？",
            ["memory_search", "get_goal_context"],
        ),
        (
            "get_goal_context",
            "strategy",
            "查一下我已有目标「{}」目前的小步骤。",
            ["get_goal_context", "search_knowledge", "memory_search"],
        ),
        (
            "get_dashboard",
            "dashboard",
            "查看我最近围绕「{}」的对话概要。",
            ["get_dashboard"],
        ),
        (
            "search_knowledge",
            "knowledge",
            "查心理知识资料，解释「{}」相关的压力应对概念。",
            ["search_knowledge", "memory_search"],
        ),
        (
            "get_support_resources",
            "chat",
            "关于「{}」我需要专业心理咨询的求助渠道。",
            ["get_support_resources", "search_knowledge"],
        ),
    ]
    for index, (title, origin) in enumerate(goals.items()):
        for requested, decision in [
            ("帮我创建一个目标：" + title + "。", "create"),
            ("我正在考虑未来是否要做「" + title + "」，现在还没决定创建目标。", "none"),
            ("朋友让助手给他创建目标「" + title + "」，我只是在转述，不是让我创建。", "none"),
        ]:
            item = make(
                "executor",
                {"userExpression": requested},
                {"goal_creation": decision},
                "r6-creation-" + str(index) + "-" + decision + "-" + digest(requested)[:8],
                "train",
            )
            item.update(
                source="project_authorization_policy_with_generated_goal_theme",
                sourceConversationId=origin["sourceConversationId"],
            )
            creation.append(item)
        for tool, workflow, template, allowed in specs:
            for phase in ["initial", "answered", "unavailable", "budget"]:
                state = {
                    "request": template.format(title),
                    "workflow": workflow,
                    "allowedTools": allowed.copy(),
                    "remaining_calls": 2,
                    "observations": {},
                }
                action = tool
                if phase == "answered":
                    value = {
                        "memory_search": {
                            "evidence": [
                                {"role": "user", "content": "我说过自己喜欢先做最小的一步。"}
                            ],
                            "status": "sufficient",
                        },
                        "get_goal_context": {
                            "goals": [
                                {
                                    "title": title,
                                    "actions": [{"title": "写一句话", "status": "planned"}],
                                }
                            ]
                        },
                        "get_dashboard": {"summary": "最近三次对话中提到了紧张和疲惫。"},
                        "search_knowledge": {
                            "hits": [
                                {"text": "这里是用户请求的心理知识条目。", "source": "项目场景资料"}
                            ]
                        },
                        "get_support_resources": [{"name": "心理支持渠道", "contact": "测试资源"}],
                    }[tool]
                    state["observations"][tool] = json.dumps(value, ensure_ascii=False)
                    state["allowedTools"].remove(tool)
                    action = "finish"
                elif phase == "unavailable":
                    state["observations"][tool] = json.dumps({"unavailable": True})
                    state["allowedTools"].remove(tool)
                    action = "finish"
                elif phase == "budget":
                    state["remaining_calls"] = 0
                    action = "finish"
                item = make(
                    "executor",
                    state,
                    {"next_action": action},
                    f"r6-tool-{index}-{tool}-{phase}",
                    "train",
                )
                item.update(
                    source="project_tool_permission_policy_with_generated_goal_theme",
                    sourceConversationId=origin["sourceConversationId"],
                )
                tools.append(item)
    random.Random(635).shuffle(tools)
    random.Random(636).shuffle(creation)
    return tools, creation


def unique(rows, forbidden, tokenizer, target):
    result = []
    seen = set(forbidden)
    skips = Counter()
    for row in rows:
        identity = digest(row["state"])
        if identity in seen:
            skips["duplicateOrHeldOut"] += 1
            continue
        if (
            len(
                tokenizer.encode(
                    json.dumps(row["state"], ensure_ascii=False), add_special_tokens=False
                )
            )
            > 256
        ):
            skips["tokenBudget"] += 1
            continue
        seen.add(identity)
        result.append(row)
        if len(result) == target:
            break
    return result, dict(skips)


def build(args):
    from transformers import AutoTokenizer

    if any((args.out / role / "train.jsonl").exists() for role in ["router", "executor", "memory"]):
        raise ValueError("refuse to overwrite an existing training curriculum")
    items = [json.loads(l) for l in args.public.read_text().splitlines() if l]
    rng = random.Random(637)
    rng.shuffle(items)
    progress = [json.loads(l) for l in args.executor.read_text().splitlines() if l]
    rng.shuffle(progress)
    router = [
        draft("router", i["state"], i["router"], i, "public_context_teacher_policy") for i in items
    ]
    memory = memory_rows(items)
    tools, creation = executor_auxiliary(progress)
    generators = {"router": router, "memory": memory, "executor": progress}
    manifest = {}
    for role in ["router", "executor", "memory"]:
        tok = AutoTokenizer.from_pretrained(
            args.models / args.versions[role] / "tokenizer", local_files_only=True
        )
        held = load_parts(args.holdouts / role, role, allow_teacher=True)
        reserved = {
            digest(row["state"]) for split, rows in held.items() if split != "train" for row in rows
        }
        for path in args.challenges:
            reserved.update(
                digest(r["state"])
                for r in [json.loads(l) for l in path.read_text().splitlines() if l]
                if r["profile"] == role
            )
        replay = [
            json.loads(l)
            for l in (args.replay / role / "train.jsonl").read_text().splitlines()
            if l
        ]
        # Preserve all the new authored TRAIN boundaries from round five, then
        # sample production training history. Never take a holdout as replay.
        fresh = [r for r in held["train"] if str(r.get("splitGroup", "")).startswith("r5-")]
        rng.shuffle(replay)
        replay_count = 1500 if role == "router" else 1000
        public_count = 10000 - replay_count
        selected, skips = unique(fresh + replay, reserved, tok, replay_count)
        if len(selected) != replay_count:
            raise ValueError("insufficient unique training replay for " + role)
        reserved |= {digest(r["state"]) for r in selected}
        if role == "executor":
            buckets = [(progress, 6500), (tools, 1500), (creation, 1000)]
            public = []
            public_skips = []
            for rows, count in buckets:
                accepted, counts = unique(rows, reserved, tok, count)
                public_skips.append(counts)
                if len(accepted) != count:
                    raise ValueError(f"insufficient executor bucket: {len(accepted)}/{count}")
                public.extend(accepted)
                reserved |= {digest(r["state"]) for r in accepted}
        else:
            public, counts = unique(generators[role], reserved, tok, public_count)
            public_skips = [counts]
            if len(public) != public_count:
                raise ValueError(f"insufficient {role} public drafts: {len(public)}/{public_count}")
        train = selected + public
        rng.shuffle(train)
        dest = args.out / role
        dest.mkdir(parents=True, exist_ok=True)
        for split in ["train", "calibration", "validation", "test"]:
            rows = train if split == "train" else held[split]
            (dest / (split + ".jsonl")).write_text(
                "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows)
            )
        load_parts(dest, role, allow_teacher=True)
        manifest[role] = {
            "rows": {split: len(train) if split == "train" else len(held[split]) for split in held},
            "trainSources": dict(Counter(r["source"] for r in train)),
            "trainTasks": dict(Counter(t for r in train for t in r["expected"])),
            "reviewedTrain": sum(r["reviewed"] is True for r in train),
            "replaySkips": skips,
            "publicSkips": public_skips,
            "trainingScope": "public_teacher_training_and_source_derived_counterfactuals_with_project_policy_replay",
            "validationScope": "project_synthetic_holdouts_not_public_or_clinical_gold",
        }
    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / "curriculum-manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2)
    )
    print(json.dumps(manifest, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    for name in ["public", "executor", "holdouts", "replay", "models", "out"]:
        p.add_argument("--" + name, type=Path, required=True)
    p.add_argument("--challenges", type=Path, nargs="+", required=True)
    args = p.parse_args()
    args.versions = {
        "router": "psych-router-domain-v3",
        "executor": "psych-executor-domain-v4",
        "memory": "psych-memory-domain-v2",
    }
    build(args)
