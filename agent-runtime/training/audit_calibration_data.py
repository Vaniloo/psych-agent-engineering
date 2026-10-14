"""Audit immutable calibration inputs; flags are review requests, not relabels."""

import argparse
import copy
import hashlib
import json
import re
from collections import Counter, defaultdict
from pathlib import Path

from psych_agent_runtime.goal_evidence import supports_progress
from psych_agent_runtime.safety import SafetyScanner

from .build_domain_decisions import TOPICS


def digest(value):
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True).encode()
    ).hexdigest()


SYNTHETIC_RECORD_STAMP = re.compile(
    r"[（(]本(?:次|条)记录(?:于|时间)\d+月\d+日\d+时\d+分[。.]?[）)]"
)


def without_record_stamp(row):
    state = semantic_state(row["state"])
    for key in ("message", "userExpression"):
        if key in state:
            state[key] = SYNTHETIC_RECORD_STAMP.sub("", state[key])
    return state


def semantic_state(state):
    """Strip persistence identifiers, preserving content, order, and quantities."""
    result = copy.deepcopy(state)
    if "candidates" in result:
        result["candidates"] = [
            {k: v for k, v in item.items() if k not in {"goalId", "actionId", "version"}}
            for item in result["candidates"]
        ]
    return result


def label_identity(row):
    result = row["expected"].copy()
    if result.get("goal_action", "none") != "none":
        index = int(result["goal_action"].removeprefix("action_"))
        item = row["state"]["candidates"][index]
        result["goal_action"] = (item["goalTitle"], item["actionTitle"])
    return result


def risk_template_proxy(row):
    """Conservative diagnostic proxy, not a proof of semantic identity.

    Only known synthetic topical/capacity suffixes are reduced. Current action
    time, negation, source, history, and dose remain intact.
    """
    state = without_record_stamp(row)
    if row.get("source") in {"project_domain_curriculum_v1", "project_router_requests_v2"}:
        text = state.get("message", "")
        text = re.sub(r"现在是\d+月\d+日，我能留出\d+分钟。", "", text)
        for topic in TOPICS:
            text = text.replace(topic, "<topic>")
        state["message"] = text
    return digest(state)


def audit(parts, role):
    by_identity = defaultdict(list)
    by_stamp_proxy = defaultdict(list)
    report = {"profile": role, "splits": {}, "flags": []}
    for split, rows in parts.items():
        categories = defaultdict(Counter)
        for row in rows:
            for task, label in row["expected"].items():
                key = "matched" if task == "goal_action" and label != "none" else label
                categories[task][key] += 1
            identity = digest(semantic_state(row["state"]))
            by_identity[identity].append((split, label_identity(row), row["splitGroup"]))
            by_stamp_proxy[digest(without_record_stamp(row))].append(
                (split, label_identity(row), row["splitGroup"])
            )
            reason = []
            if role == "router":
                scan = SafetyScanner().scan(row["state"]["message"])
                if scan.high_risk and row["expected"]["risk_band"] != "urgent":
                    reason.append("guard_detects_danger_but_label_not_urgent")
                if scan.attributed_only and row["expected"]["risk_band"] == "urgent":
                    reason.append("attribution_guard_disagrees_with_urgent_label")
            elif row["expected"].get("goal_action", "none") != "none":
                index = int(row["expected"]["goal_action"].removeprefix("action_"))
                title = row["state"]["candidates"][index]["actionTitle"]
                if not supports_progress(
                    row["state"]["userExpression"], title, row["expected"]["goal_outcome"]
                ):
                    reason.append("positive_label_not_supported_by_current_literal_guard")
            if reason:
                report["flags"].append(
                    {
                        "split": split,
                        "splitGroup": row["splitGroup"],
                        "source": row["source"],
                        "reasons": reason,
                        "state": row["state"],
                        "expected": row["expected"],
                    }
                )
        report["splits"][split] = {
            "records": len(rows),
            "declaredGroups": len({r["splitGroup"] for r in rows}),
            "semanticInputsWithoutPersistenceIds": len(
                {digest(semantic_state(r["state"])) for r in rows}
            ),
            "withoutRecordStampProxies": len({digest(without_record_stamp(r)) for r in rows}),
            "riskTemplateProxies": len({risk_template_proxy(r) for r in rows})
            if role == "router"
            else None,
            "sources": dict(Counter(r["source"] for r in rows)),
            "categories": dict(categories),
            "independentReviewVerified": False,
        }
    conflicts, cross_split = [], []
    for identity, values in by_identity.items():
        if len({digest(labels) for _, labels, _ in values}) > 1:
            conflicts.append({"identity": identity, "occurrences": values})
        if len({split for split, _, _ in values}) > 1:
            cross_split.append({"identity": identity, "occurrences": values})
    report.update(
        contradictoryLabelsOnSameContent=conflicts,
        sameContentAcrossSplits=cross_split,
        recordStampOnlySeparationAcrossSplits=[
            {"identity": identity, "occurrences": values}
            for identity, values in by_stamp_proxy.items()
            if len({split for split, _, _ in values}) > 1
        ],
        flagsByReason=dict(Counter(reason for r in report["flags"] for reason in r["reasons"])),
        scope="Guard disagreements need review: guards can also be wrong. No labels, inputs or old splits modified. Risk template proxies are a dependence diagnostic only.",
    )
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    if args.out.exists():
        raise ValueError("refuse to overwrite audit")
    result = {}
    for role in ("router", "executor"):
        paths = {
            split: args.dataset / role / (split + ".jsonl")
            for split in ("train", "calibration", "validation", "test")
        }
        parts = {
            split: [json.loads(line) for line in path.read_text().splitlines() if line]
            for split, path in paths.items()
        }
        result[role] = {
            **audit(parts, role),
            "sourceSha256": {
                s: hashlib.sha256(p.read_bytes()).hexdigest() for s, p in paths.items()
            },
        }
    args.out.write_text(json.dumps(result, ensure_ascii=False, indent=2))
    print(
        json.dumps(
            {
                r: {
                    k: v
                    for k, v in a.items()
                    if k
                    not in {"flags", "sameContentAcrossSplits", "contradictoryLabelsOnSameContent"}
                }
                for r, a in result.items()
            },
            ensure_ascii=False,
            indent=2,
        )
    )
