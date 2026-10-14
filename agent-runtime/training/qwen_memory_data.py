"""Build a bounded, source-aware seven-task course from existing Memory splits.

Preserves original split priority and declared groups, removes record stamps,
tracking fields, inconsistent duplicate labels and known challenge overlap.
Program labels remain program labels; no teacher/API relabeling.
"""

import argparse
import hashlib
import json
import random
import re
from collections import Counter, defaultdict
from pathlib import Path

from psych_agent_runtime.qwen_memory_contract import INSTRUCTIONS, normalize

STAMP = re.compile(r"[（(]本(?:次|条)记录(?:于|时间)\d+月\d+日\d+时\d+分[。.]?[）)]")
SPLITS = ("test", "validation", "calibration", "train")


def digest(value):
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, ensure_ascii=False).encode()
    ).hexdigest()


def clean(value):
    if isinstance(value, str):
        return STAMP.sub("", value)
    if isinstance(value, dict):
        return {k: clean(v) for k, v in value.items()}
    if isinstance(value, list):
        return [clean(v) for v in value]
    return value


def build(root, challenge, out, limit=300):
    if out.exists():
        raise ValueError("refuse to overwrite a frozen course")
    records, exclusions = [], Counter()
    source_hashes = {}
    for folder, tasks in [
        ("pair-round7-final/memory", set(INSTRUCTIONS) - {"evidence_sufficient"}),
        ("sufficiency-round8/memory", {"evidence_sufficient"}),
    ]:
        for split in SPLITS:
            path = root / folder / (split + ".jsonl")
            raw = path.read_bytes()
            source_hashes[str(path.relative_to(root))] = hashlib.sha256(raw).hexdigest()
            for index, line in enumerate(raw.decode().splitlines()):
                row = json.loads(line)
                if (
                    row.get("reviewed") is not True
                    or row.get("annotationStatus") == "teacher_draft"
                ):
                    exclusions["unreviewed_or_teacher_draft"] += 1
                    continue
                for task, label in row["expected"].items():
                    if task not in tasks or label not in {"yes", "no"}:
                        continue
                    try:
                        state = clean(normalize(task, row["state"]))
                    except (ValueError, TypeError):
                        exclusions["missing_source_or_invalid_state"] += 1
                        continue
                    records.append(
                        {
                            "task": task,
                            "state": state,
                            "label": label,
                            "split": split,
                            "family": row.get("splitGroup") or f"{folder}:{split}:{index}",
                            "source": row.get("source", "unknown"),
                            "annotationStatus": row.get("annotationStatus", "unknown"),
                            "reviewedBy": row.get("reviewedBy"),
                        }
                    )
    forbidden = set()
    for row in challenge:
        for task in row["expected"]:
            forbidden.add(digest(clean(normalize(task, row["state"]))))
    parent = {}

    def find(x):
        parent.setdefault(x, x)
        if parent[x] != x:
            parent[x] = find(parent[x])
        return parent[x]

    def union(a, b):
        a, b = find(a), find(b)
        if a != b:
            parent[b] = a

    labels = defaultdict(set)
    for row in records:
        identity = digest(row["state"])
        union("family:" + row["family"], "input:" + identity)
        labels[(row["task"], identity)].add(row["label"])
    bad = {find("input:" + i) for (t, i), values in labels.items() if len(values) > 1}
    bad |= {find("input:" + i) for i in forbidden if "input:" + i in parent}
    priority = {}
    for row in records:
        group = find("family:" + row["family"])
        rank = SPLITS.index(row["split"])
        priority[group] = min(priority.get(group, rank), rank)
    parts = {s: [] for s in SPLITS}
    seen = set()
    for row in records:
        group = find("family:" + row["family"])
        identity = (row["task"], digest(row["state"]))
        if group in bad:
            exclusions["conflict_or_challenge_group"] += 1
        elif SPLITS.index(row["split"]) != priority[group]:
            exclusions["cross_split_group_lower_priority"] += 1
        elif identity in seen:
            exclusions["duplicate_task_input"] += 1
        else:
            seen.add(identity)
            parts[row["split"]].append({**row, "family": digest(group)})
    rng = random.Random(1412)
    for split, rows in parts.items():
        selected = []
        for task in INSTRUCTIONS:
            for label in ("yes", "no"):
                subset = [r for r in rows if r["task"] == task and r["label"] == label]
                rng.shuffle(subset)
                selected.extend(subset[: limit if split == "train" else 100])
        rng.shuffle(selected)
        parts[split] = selected
    hashes, counts = {}, {}
    out.mkdir(parents=True)
    for split, rows in parts.items():
        raw = "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows).encode()
        (out / (split + ".jsonl")).write_bytes(raw)
        hashes[split] = hashlib.sha256(raw).hexdigest()
        counts[split] = {
            "rows": len(rows),
            "families": len({r["family"] for r in rows}),
            "tasks": dict(Counter(r["task"] + ":" + r["label"] for r in rows)),
        }
    manifest = {
        "hashes": hashes,
        "counts": counts,
        "sourceHashes": source_hashes,
        "exclusions": dict(exclusions),
        "instructionsSha256": digest(INSTRUCTIONS),
        "epochs": 1,
        "teacherApiUsed": False,
        "deploymentEnabled": False,
        "scope": "Original split/group priority and exact normalized input isolation only. Authored/programmatic labels, not independent human/clinical gold. Shared semantic templates remain. Challenge inputs/groups excluded from all training/course splits.",
    }
    (out / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps(manifest, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--challenge", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    build(args.root, [json.loads(l) for l in args.challenge.read_text().splitlines()], args.out)
