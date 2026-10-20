"""Export one owner's suspected read issues for review, never automatic training labels."""

import argparse
import hashlib
import json
import os
import sqlite3
from pathlib import Path


def collect(database, owner, *, include_dialogue=False, limit=200):
    uri = Path(database).resolve().as_uri() + "?mode=ro"
    cases = []
    with sqlite3.connect(uri, uri=True) as db:
        try:
            rows = db.execute("SELECT request_id,observed_at,metadata FROM read_audits WHERE owner=? "
                              "ORDER BY observed_at DESC LIMIT ?", (owner, min(200, max(1, limit)))).fetchall()
        except sqlite3.OperationalError as exc:
            if "no such table: read_audits" in str(exc):
                return []
            raise
        for request_id, observed_at, metadata in rows:
            case = {"caseId": hashlib.sha256(f"{owner}:{request_id}".encode()).hexdigest()[:16],
                    "observedAt": observed_at, "origin": "runtime_review_signal",
                    "verdict": "unreviewed", "trainingEligible": False, "audit": json.loads(metadata)}
            if include_dialogue:
                # Same owner + exact request, not a nearest-time cross-session join.
                case["dialogue"] = [{"role": role, "content": content} for role, content in db.execute(
                    "SELECT role,content FROM observations WHERE owner=? AND source=? ORDER BY role DESC",
                    (owner, request_id))]
            cases.append(case)
    return cases


def export(database, owner, out, *, include_dialogue=False):
    cases = collect(database, owner, include_dialogue=include_dialogue)
    out = Path(out)
    out.mkdir(mode=0o700, parents=True, exist_ok=False)
    out.chmod(0o700)
    destination = out / "cases.jsonl"
    with open(destination, "x", opener=lambda path, flags: os.open(path, flags, 0o600)) as stream:
        for case in cases:
            stream.write(json.dumps(case, ensure_ascii=False) + "\n")
    return {"cases": len(cases), "classification": "suspected_requires_review", "trainingEligible": False,
            "includesDialogue": include_dialogue}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--owner", required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--include-dialogue", action="store_true")
    args = parser.parse_args()
    print(json.dumps(export(args.database, args.owner, args.out, include_dialogue=args.include_dialogue)))
