"""Source-aware sufficiency curriculum; no challenge rows or teacher calls.

Source groups retain their partition. Labels from original datasets retain their
review status. New wording families are synthetic project policy, not clinical.
"""

import argparse
import json
from collections import Counter
from pathlib import Path

from psych_agent_runtime.decision_contract import MEMORY_EVIDENCE_CONTRACT, memory_evidence_state
from training.decision_data import load_parts
from training.refine_decisions import make
from training.refine_pair_round import SPLITS, identity


def converted(row):
    state = json.loads(json.dumps(row["state"]))
    for item in state.get("evidence", []):
        # This conversion is only for authored legacy curricula with user evidence.
        # New runtime v4 requests require an explicit role and reject missing roles.
        if "role" not in item:
            if row.get("source") not in {
                "project_domain_curriculum_v1",
                "project_memory_evidence_v2",
                "authored_boundary_contract_v3",
            }:
                raise ValueError("source role missing in public evidence")
            item["role"] = "assistant" if item["content"].startswith("助手建议：") else "user"
    return {
        **row,
        "contractVersion": MEMORY_EVIDENCE_CONTRACT,
        "state": memory_evidence_state(state),
        "expected": {"evidence_sufficient": row["expected"]["evidence_sufficient"]},
    }


def fixtures(split):
    family = SPLITS.index(split)
    activities = [
        ["游泳", "跑步", "弹琴", "画画"],
        ["骑车", "读书", "写作", "舞蹈"],
        ["瑜伽", "健走", "烘焙", "园艺"],
        ["唱歌", "太极", "练字", "攀岩"],
    ][family]
    english = [
        ["swimming", "piano", "running", "painting"],
        ["cycling", "reading", "writing", "dancing"],
        ["yoga", "walking", "baking", "gardening"],
        ["singing", "chess", "hiking", "pottery"],
    ][family]
    out = []

    def emit(query, evidence, answer, tag):
        state = {"query": query, "evidence": evidence}
        row = make(
            "memory",
            state,
            {"evidence_sufficient": answer},
            "suff-policy-" + split + "-" + tag + "-" + identity(state),
            split,
        )
        row.update(
            contractVersion=MEMORY_EVIDENCE_CONTRACT,
            source="source_aware_sufficiency_policy",
            validationScope="synthetic_sufficiency_policy_not_clinical",
        )
        out.append(row)

    for language, names in [("zh", activities), ("en", english)]:
        for activity in names:
            for count in range(1, 9):
                if language == "zh":
                    query = f"我每周{activity}几次，每次多久？"
                    one = f"我每周{activity}{count}次。"
                    two = f"我每次{activity}{count + 10}分钟。"
                    whole = f"我每周{activity}{count}次，每次{count + 10}分钟。"
                    other = f"朋友每周{activity}{count}次，每次{count + 10}分钟，这是对方的习惯。"
                    denial = f"我本周没有{activity}。"
                    negative_query = f"我本周有{activity}吗？"
                else:
                    query = f"How often do I do {activity} each week and how long is each session?"
                    one = f"I do {activity} {count} times per week."
                    two = f"Each {activity} session lasts {count + 10} minutes."
                    whole = (
                        f"I do {activity} {count} times weekly for {count + 10} minutes each time."
                    )
                    other = f"My friend does {activity} {count} times weekly for {count + 10} minutes. This is my friend's routine."
                    denial = f"I did not do {activity} this week."
                    negative_query = f"Did I do {activity} this week?"
                u = lambda text: {"role": "user", "content": text}
                a = lambda text: {"role": "assistant", "content": text}
                for records, answer, tag in [
                    ([u(whole)], "yes", "full"),
                    ([a(whole)], "no", "assistant"),
                    ([u(one)], "no", "missing-duration"),
                    ([u(two)], "no", "missing-count"),
                    ([u(one), u(two)], "yes", "combined"),
                    ([u(one), a(two)], "no", "mixed-incomplete"),
                    ([u(other)], "no", "third-party"),
                    ([], "no", "empty"),
                ]:
                    emit(query, records, answer, tag)
                emit(negative_query, [u(denial)], "yes", "owned-denial")
                emit(
                    query,
                    [u(whole), u(whole.replace(str(count + 10), str(count + 30)))],
                    "no",
                    "unresolved-conflict",
                )
    return out


def build(source, out):
    if out.exists():
        raise ValueError("refuse to overwrite sufficiency curriculum")
    parts = {}
    seen = set()
    groups = set()
    # Project normalized states before splitting; earlier held-outs take priority.
    for split in ["test", "validation", "calibration", "train"]:
        rows = [
            converted(json.loads(line))
            for line in (source / "memory" / f"{split}.jsonl").read_text().splitlines()
            if line and "evidence_sufficient" in json.loads(line)["expected"]
        ]
        extra = []
        for row in rows:
            if (
                row["expected"]["evidence_sufficient"] == "yes"
                and row["state"]["evidence"]
                and all(c["role"] == "user" for c in row["state"]["evidence"])
            ):
                extra.append(
                    {
                        **row,
                        "state": {
                            "query": row["state"]["query"],
                            "evidence": [
                                {"role": "assistant", "content": c["content"]}
                                for c in row["state"]["evidence"]
                            ],
                        },
                        "expected": {"evidence_sufficient": "no"},
                        "source": row.get("source", "") + "__assistant_source_counterfactual",
                    }
                )
        rows += extra + fixtures(split)
        selected = []
        local = set()
        group_names = set()
        for row in rows:
            key = identity(row["state"])
            if key in seen or key in local or row["splitGroup"] in groups:
                continue
            selected.append(row)
            local.add(key)
            group_names.add(row["splitGroup"])
        parts[split] = selected
        seen.update(local)
        groups.update(group_names)
    (out / "memory").mkdir(parents=True)
    for split in SPLITS:
        (out / "memory" / f"{split}.jsonl").write_text(
            "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in parts[split])
        )
    load_parts(out / "memory", "memory", True, MEMORY_EVIDENCE_CONTRACT)
    manifest = {
        "memory": {
            "trainingScope": "isolated_source_aware_sufficiency_with_normalized_evidence",
            "contract": MEMORY_EVIDENCE_CONTRACT,
            "rows": {s: len(parts[s]) for s in SPLITS},
            "trainSources": dict(Counter(r["source"] for r in parts["train"])),
            "trainLabels": dict(
                Counter(r["expected"]["evidence_sufficient"] for r in parts["train"])
            ),
            "review": "Original teacher labels remain drafts. Counterfactuals inherit review status. Synthetic held-outs are project policy only.",
            "source": str(source),
            "challengeRowsUsedForTraining": False,
        }
    }
    (out / "curriculum-manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2)
    )
    return manifest


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--source", type=Path, required=True)
    p.add_argument("--out", type=Path, required=True)
    a = p.parse_args()
    print(json.dumps(build(a.source, a.out), ensure_ascii=False, indent=2))
