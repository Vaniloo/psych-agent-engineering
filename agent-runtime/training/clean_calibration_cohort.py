"""Create a new immutable cohort; preserve labels and quarantine split overlaps.

Persistence IDs and artificial record stamps do not establish independence.
Risk topical/capacity variants from known generators are conservatively grouped.
This remains project-policy data, not independently reviewed clinical gold.
"""

import argparse
import hashlib
import json
from collections import Counter
from pathlib import Path

from .audit_calibration_data import digest, risk_template_proxy, without_record_stamp


def group_identity(row, role):
    return risk_template_proxy(row) if role == "router" else digest(without_record_stamp(row))


def clean(parts, train_rows, role):
    blocked = {group_identity(row, role) for row in train_rows}
    chosen, excluded = {}, []
    # Test has first claim, then validation, then calibration. No predictions or
    # test labels participate in this assignment; only input/group identities.
    for split in ("test", "validation", "calibration"):
        rows = []
        seen = set()
        for row in parts[split]:
            identity = group_identity(row, role)
            if identity in blocked or identity in seen:
                excluded.append(
                    {
                        "split": split,
                        "splitGroup": row["splitGroup"],
                        "reason": "train_or_priority_split_overlap"
                        if identity in blocked
                        else "same_input_family_in_split",
                        "groupIdentity": identity,
                    }
                )
                continue
            seen.add(identity)
            rows.append(
                {
                    **row,
                    "calibrationGroup": role + ":" + row["splitGroup"],
                    "inputIsolationIdentity": identity,
                    "calibrationReviewScope": "input_isolation_audit_only_not_independent_label_review",
                }
            )
        blocked.update(seen)
        chosen[split] = rows
    return chosen, excluded


def build(datasets, out):
    if out.exists():
        raise ValueError("refuse to overwrite clean cohort")
    out.mkdir(parents=True)
    manifest = {}
    for role, base in [("router", "pair-round7-final"), ("executor", "three-laya-public-r6")]:
        original = datasets / "task-optimization-round11" / role
        paths = {
            s: original / (s + ".jsonl") for s in ("train", "calibration", "validation", "test")
        }
        parts = {
            s: [json.loads(line) for line in p.read_text().splitlines() if line]
            for s, p in paths.items()
        }
        base_train = datasets / base / role / "train.jsonl"
        trained = parts["train"] + [
            json.loads(line) for line in base_train.read_text().splitlines() if line
        ]
        chosen, excluded = clean(parts, trained, role)
        dest = out / role
        dest.mkdir()
        counts, labels, hashes = {}, {}, {}
        for split, rows in chosen.items():
            raw = "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows).encode()
            (dest / (split + ".jsonl")).write_bytes(raw)
            counts[split] = len(rows)
            labels[split] = {
                t: dict(
                    Counter(
                        "matched"
                        if t == "goal_action" and row["expected"][t] != "none"
                        else row["expected"][t]
                        for row in rows
                        if t in row["expected"]
                    )
                )
                for t in {t for row in rows for t in row["expected"]}
            }
            hashes[split] = hashlib.sha256(raw).hexdigest()
        (dest / "exclusions.json").write_text(json.dumps(excluded, indent=2))
        manifest[role] = {
            "rows": counts,
            "labels": labels,
            "excluded": len(excluded),
            "datasetSha256": hashes,
            "oldSourceSha256": {
                s: hashlib.sha256(p.read_bytes()).hexdigest() for s, p in paths.items()
            },
            "baseTrainSha256": hashlib.sha256(base_train.read_bytes()).hexdigest(),
        }
    manifest.update(
        independentReviewVerified=False,
        labelsChanged=False,
        scope="New cohort built without predictions; old files untouched. Input/group isolation does not prove semantic or clinical independence.",
    )
    (out / "cohort-manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2))
    return manifest


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--datasets", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(build(args.datasets, args.out), ensure_ascii=False, indent=2))
