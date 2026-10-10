"""Freeze bounded source-ownership diagnostics; never report original QA accuracy."""

import argparse
import hashlib
import json
import random
from collections import Counter
from pathlib import Path


def main():
    from transformers import AutoTokenizer

    p = argparse.ArgumentParser()
    p.add_argument("--raw", type=Path, required=True)
    p.add_argument("--checkpoint", type=Path, required=True)
    p.add_argument("--out", type=Path, required=True)
    a = p.parse_args()
    if a.out.exists():
        raise SystemExit("Refuse to overwrite frozen evaluation")
    tok = AutoTokenizer.from_pretrained(a.checkpoint / "tokenizer", local_files_only=True)
    records = json.loads(a.raw.read_text())
    random.Random(642).shuffle(records)
    rows = []
    skipped = Counter()
    questions = 0
    for r in records:
        if r["question_type"] not in {"single-session-user", "single-session-preference"} or r[
            "question_id"
        ].endswith("_abs"):
            continue
        gold = [
            {"role": t["role"], "content": t["content"]}
            for session in r["haystack_sessions"]
            for t in session
            if t.get("has_answer")
        ]
        if len(gold) != 1 or gold[0]["role"] != "user":
            skipped["notOneUserAnswerTurn"] += 1
            continue
        query = r["question"]
        fact = gold[0]["content"]
        cs = [{"role": "assistant", "content": fact}, gold[0]]
        random.Random(questions + 649).shuffle(cs)
        state = {"query": query, "candidates": cs}
        sufficient = {"query": query, "evidence": gold}
        negative = {"query": query, "evidence": [{"role": "assistant", "content": fact}]}
        if any(
            len(tok.encode(json.dumps(s, ensure_ascii=False), add_special_tokens=False)) > 256
            for s in [state, sufficient, negative]
        ):
            skipped["wholeStateTokenBudget"] += 1
            continue
        name = "longmemeval-adapted-" + r["question_id"]
        rows.append(
            {
                "profile": "memory",
                "name": name + "-relevance",
                "state": state,
                "expected": {
                    f"relevance_{i}": "yes" if c["role"] == "user" else "no"
                    for i, c in enumerate(cs)
                },
                "source": "LongMemEval official has_answer user evidence; assistant clone is counterfactual",
            }
        )
        rows.append(
            {
                "profile": "memory",
                "name": name + "-sufficient",
                "state": sufficient,
                "expected": {"evidence_sufficient": "yes"},
                "source": "LongMemEval single-session official user answer-bearing turn",
            }
        )
        rows.append(
            {
                "profile": "memory",
                "name": name + "-assistant-clone",
                "state": negative,
                "expected": {"evidence_sufficient": "no"},
                "source": "Source-derived assistant-ownership counterfactual; not original benchmark question",
            }
        )
        questions += 1
        if questions >= 30:
            break
    if questions < 8:
        raise SystemExit("Insufficient bounded benchmark inputs")
    a.out.parent.mkdir(parents=True, exist_ok=True)
    a.out.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows))
    manifest = {
        "scope": "Adapted short-context evidence ownership diagnostics only; NOT original LongMemEval QA accuracy; official single-session-user/preference answer-bearing user turns; no training use",
        "benchmarkRevision": "98d7416c24c778c2fee6e6f3006e7a073259d48f",
        "sourceQuestions": questions,
        "rows": len(rows),
        "skipped": dict(skipped),
        "tokenBudget": 256,
        "sha256": hashlib.sha256(a.out.read_bytes()).hexdigest(),
    }
    a.out.with_suffix(".manifest.json").write_text(json.dumps(manifest, indent=2))
    print(json.dumps(manifest), flush=True)


if __name__ == "__main__":
    main()
