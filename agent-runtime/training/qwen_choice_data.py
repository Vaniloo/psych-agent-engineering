"""Freeze grouped Router/Executor courses; no API relabeling or challenge training."""

import argparse
import hashlib
import json
import random
from collections import Counter, defaultdict
from pathlib import Path

from psych_agent_runtime.decision_contract import profile_contract
from psych_agent_runtime.qwen_choice_contract import choices, normalize, renderer_spec

from .qwen_memory_data import clean, digest

SPLITS = ("test", "validation", "calibration", "train")
SOURCES = ("three-laya-public-r6", "semantic-contrast-round10", "task-optimization-round11")


def build(root, profile, challenge, out, limit=400):
    if out.exists():
        raise ValueError("refuse to overwrite frozen course")
    _, tasks = profile_contract(profile)
    records = []
    excluded = Counter()
    source_hashes = {}
    for folder in SOURCES:
        for split in SPLITS:
            path = root / folder / profile / (split + ".jsonl")
            if not path.exists():
                continue
            blob = path.read_bytes()
            source_hashes[str(path.relative_to(root))] = hashlib.sha256(blob).hexdigest()
            for i, line in enumerate(blob.decode().splitlines()):
                row = json.loads(line)
                if (
                    row.get("reviewed") is not True
                    or row.get("annotationStatus") == "teacher_draft"
                ):
                    excluded["unreviewed_or_teacher_draft"] += 1
                    continue
                for task, label in row["expected"].items():
                    if task not in tasks or not isinstance(label, str):
                        continue
                    try:
                        state = clean(normalize(profile, task, row["state"]))
                        if label not in choices(profile, task, state):
                            raise ValueError("label not authorized")
                    except (ValueError, TypeError, KeyError):
                        excluded["invalid_or_unauthorized_label"] += 1
                        continue
                    records.append(
                        {
                            "profile": profile,
                            "task": task,
                            "state": state,
                            "label": label,
                            "split": split,
                            "family": row.get("splitGroup") or f"{folder}:{split}:{i}",
                            "source": row.get("source", "unknown"),
                            "annotationStatus": row.get("annotationStatus", "unknown"),
                        }
                    )
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
    bad = {find("input:" + i) for (task, i), values in labels.items() if len(values) > 1}
    for row in challenge:
        if row["profile"] != profile:
            continue
        for task in row["expected"]:
            identity = digest(clean(normalize(profile, task, row["state"])))
            if "input:" + identity in parent:
                bad.add(find("input:" + identity))
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
            excluded["conflict_or_challenge_component"] += 1
        elif SPLITS.index(row["split"]) != priority[group]:
            excluded["cross_split_lower_priority"] += 1
        elif identity in seen:
            excluded["duplicate_task_input"] += 1
        else:
            seen.add(identity)
            parts[row["split"]].append({**row, "family": digest(group)})
    rng = random.Random(1413 if profile == "router" else 1414)
    out.mkdir(parents=True)
    hashes = {}
    counts = {}
    for split, rows in parts.items():
        grouped = defaultdict(list)
        for row in rows:
            grouped[(row["task"], row["label"])].append(row)
        kept = []
        for key, subset in sorted(grouped.items()):
            rng.shuffle(subset)
            kept.extend(subset[: limit if split == "train" else 100])
        rng.shuffle(kept)
        blob = "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in kept).encode()
        (out / (split + ".jsonl")).write_bytes(blob)
        hashes[split] = hashlib.sha256(blob).hexdigest()
        counts[split] = {
            "rows": len(kept),
            "families": len({r["family"] for r in kept}),
            "tasks": dict(Counter(r["task"] + ":" + r["label"] for r in kept)),
        }
    manifest = {
        "profile": profile,
        "epochs": 1,
        "hashes": hashes,
        "counts": counts,
        "exclusions": dict(excluded),
        "sourceHashes": source_hashes,
        "rendererSha256": digest(renderer_spec(profile)),
        "teacherApiUsed": False,
        "deploymentEnabled": False,
        "scope": "Declared component and normalized-input isolation; program labels and shared templates, not independent human or clinical gold. Challenge components excluded from all course splits.",
    }
    (out / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps(manifest, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--profile", choices=["router", "executor"], required=True)
    parser.add_argument("--challenge", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    build(
        args.root,
        args.profile,
        [json.loads(l) for l in args.challenge.read_text().splitlines()],
        args.out,
    )
