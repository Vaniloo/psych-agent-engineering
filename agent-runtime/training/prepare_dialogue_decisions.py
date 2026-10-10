"""Prepare public dialogues for local Router Laya decision annotation, not response imitation.

Future assistant turns are excluded. Labels intentionally remain empty until
reviewed: public counseling dialogues do not carry this application's labels.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import random
from collections import Counter
from pathlib import Path

from psych_agent_runtime.decision_contract import ROUTER_QUESTIONS
from psych_agent_runtime.routing_contract import routing_state


def messages_from_row(row: dict) -> list[dict[str, str]]:
    if "messages" in row:
        return [
            {"role": item["role"], "content": str(item["content"])}
            for item in row["messages"]
            if item.get("role") in {"user", "assistant"}
        ]
    messages = []
    for user, assistant in row.get("history", []):
        messages.extend(
            [
                {"role": "user", "content": str(user)},
                {"role": "assistant", "content": str(assistant)},
            ]
        )
    if row.get("instruction"):
        messages.append({"role": "user", "content": str(row["instruction"])})
    if row.get("output"):
        messages.append({"role": "assistant", "content": str(row["output"])})
    return messages


def prepare(sources: list[Path], limit: int = 1000, seed: int = 42) -> tuple[list[dict], dict]:
    cases, seen = [], set()
    provenance = []
    for source in sources:
        raw = source.read_bytes()
        provenance.append({"file": source.name, "sha256": hashlib.sha256(raw).hexdigest()})
        for row in json.loads(raw):
            messages = messages_from_row(row)
            if not messages:
                continue
            identity = json.dumps(messages, ensure_ascii=False, sort_keys=True).encode()
            conversation_id = hashlib.sha256(identity).hexdigest()
            if conversation_id in seen:
                continue
            seen.add(conversation_id)
            bucket = int(conversation_id[:8], 16) % 100
            split = (
                "train"
                if bucket < 70
                else "calibration"
                if bucket < 80
                else "validation"
                if bucket < 90
                else "test"
            )
            for index, item in enumerate(messages):
                if item["role"] != "user" or not item["content"].strip():
                    continue
                cases.append(
                    {
                        "caseId": f"{conversation_id[:20]}-{index}",
                        "conversationId": conversation_id,
                        "split": split,
                        "source": source.name,
                        "state": routing_state(item["content"], messages[:index]),
                        "questions": ROUTER_QUESTIONS,
                        "expected": {name: None for name in ROUTER_QUESTIONS},
                        "reviewed": False,
                    }
                )
    random.Random(seed).shuffle(cases)
    cases = cases[:limit]
    manifest = {
        "task": "psych-router-v2",
        "reviewed": False,
        "seed": seed,
        "items": len(cases),
        "splits": dict(Counter(item["split"] for item in cases)),
        "sourceFiles": provenance,
        "labels": "pending_annotation_not_training_ready",
    }
    return cases, manifest


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--sources", type=Path, nargs="+", required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--limit", type=int, default=1000)
    args = parser.parse_args()
    cases, manifest = prepare(args.sources, args.limit)
    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / "annotation-cases.jsonl").write_text(
        "".join(json.dumps(case, ensure_ascii=False) + "\n" for case in cases), encoding="utf-8"
    )
    (args.out / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print(json.dumps(manifest, ensure_ascii=False, indent=2))
