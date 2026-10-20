"""Authored development ablation: identical history, bounded retrieval, real models.

This is not a vector-retrieval comparison or an independent test set. Synthetic
ledger writes use the actual lifecycle controller, never gold write decisions.
"""

import argparse
import asyncio
import json
import os
import time
from pathlib import Path

from psych_agent_runtime.config import Settings
from psych_agent_runtime.memory import MemoryService
from psych_agent_runtime.memory_projection import MemoryProjection
from psych_agent_runtime.memory_query import recall_only_question
from psych_agent_runtime.memory_read import MemoryReadView, select_candidates
from psych_agent_runtime.memory_shadow import MemoryLifecycleShadow

HISTORY = [
    "我平时喜欢听轻音乐，我唯一一只兔子叫芝麻团。",
    "我平时喜欢听轻音乐。",
    "更正：我平时喜欢听的是民谣，不是轻音乐。",
    "我的职业是教师，我今天有点开心。",
    "我平时最喜欢去森林步道散步。",
    "我朋友小林喜欢听爵士乐。",
    "你记得我以前喜欢听什么吗？",
    "我昨天有点难过，今天已经高兴起来了。",
]
# Expected strings are evidence diagnostics, not generated-answer/clinical ratings.
CASES = [
    ("current_music", "我现在平时喜欢听什么音乐？", ["民谣"], ["我平时喜欢听轻音乐"]),
    (
        "music_paraphrase",
        "放松时给我挑点符合我偏好的音乐，我喜欢哪种？",
        ["民谣"],
        ["我平时喜欢听轻音乐"],
    ),
    ("pet", "我养的兔子叫什么？", ["芝麻团"], []),
    ("pet_paraphrase", "你还记得我家那只兔子的名字吗？", ["芝麻团"], []),
    ("occupation", "我的职业是什么？", ["教师"], []),
    ("occupation_paraphrase", "我是做什么工作的？", ["教师"], []),
    ("mood", "我今天心情怎么样？", ["开心"], []),
    ("mood_change", "我昨天心情怎样，今天有什么变化？", ["难过", "高兴"], []),
    ("outdoor", "我平时喜欢去哪里散步？", ["森林步道"], []),
    ("two_facets", "我的兔子叫什么，我是做什么工作的？", ["芝麻团", "教师"], []),
    ("friend", "小林喜欢听什么？", ["爵士乐"], []),
    ("unknown_address", "我的家庭住址是哪里？", [], ["*"]),
    ("unknown_birthday", "我的生日是几月几号？", [], ["*"]),
]


def bounded(records, max_chars=1600):
    result, remaining = [], max_chars
    for record in records[:8]:
        if remaining <= 0:
            break
        text = record["content"][:remaining]
        result.append(
            {
                **record,
                "content": text,
                "truncated": record.get("truncated", False) or len(text) < len(record["content"]),
            }
        )
        remaining -= len(text)
    return result


def grade(records, required, forbidden):
    text = "\n".join(r["content"] for r in records)
    missing = [term for term in required if term not in text]
    invalid = bool(records) if forbidden == ["*"] else any(term in text for term in forbidden)
    return {
        "matched": not missing and not invalid,
        "missing": missing,
        "staleOrIrrelevant": invalid,
    }


async def run(args):
    if args.out.exists():
        raise ValueError("refuse to overwrite read diagnostic")
    args.out.mkdir(parents=True)
    fixture = json.loads(args.fixture.read_text()) if args.fixture else None
    history = [r["message"] for r in fixture["history"]] if fixture else HISTORY
    cases = (
        [(r["id"], r["query"], r["required"], r["forbidden"]) for r in fixture["queries"]]
        if fixture
        else CASES
    )
    service = MemoryService(
        Settings(
            memory_provider="qwen",
            memory_contract_version="psych-memory-v4",
            memory_base_url=args.url,
            memory_api_key=os.environ[args.key_env],
            memory_timeout_seconds=20,
            memory_store_path=str(args.out / "raw.sqlite3"),
            memory_lifecycle_shadow_url="",
            memory_lifecycle_shadow_users="",
            memory_lifecycle_sandbox_users="",
            memory_lifecycle_read_users="",
            memory_lifecycle_active_users="",
        )
    )
    from psych_agent_runtime.memory_ledger import MemoryLedger

    ledger = MemoryLedger(args.out / "ledger.sqlite3")
    from psych_agent_runtime.ollama_client import OllamaClient

    generation = OllamaClient(Settings()) if args.source_review else None
    review_calls = 0
    if generation:
        original_chat = generation.chat_json

        async def counted_chat(*a, **kw):
            nonlocal review_calls
            review_calls += 1
            return await original_chat(*a, **kw)

        generation.chat_json = counted_chat
    shadow = MemoryLifecycleShadow(
        ledger,
        args.lifecycle_url,
        os.environ[args.lifecycle_key_env],
        timeout=20,
        expected_sha=args.expected_sha,
        source_reviewer=generation,
        review_model=Settings().generation_model,
        review_provider=Settings().ai_provider,
    )
    owner = "synthetic-read-comparison"
    writer, writes, rows = (
        MemoryProjection(ledger, {owner}, require_source_review=args.source_review),
        [],
        [],
    )
    try:
        for index, text in enumerate(history):
            job = {
                "username": owner,
                "sessionId": index + 1,
                "requestId": f"read-{index}",
                "userMessage": text,
                "assistantReply": "合成测试回复。",
                "createdAt": f"{fixture.get('observedDate', '2026-10-16') if fixture else '2026-10-16'}T12:{index:02d}:00+08:00",
            }
            await service.ingest(job)
            preview = await shadow.inspect(job)
            writes.append(
                {"message": text, "preview": preview, "projection": writer.project(job, preview)}
            )
            print("history", index + 1, [p["action"] for p in preview["proposals"]], flush=True)
        # Foreign facts are deliberately absent from the owned read view.
        foreign = {
            **job,
            "username": "other-account",
            "requestId": "foreign",
            "userMessage": "我的家庭住址是隔壁市。",
        }
        service.store.ingest(foreign)
        raw = [r for r in service.store.read_pool(owner) if not recall_only_question(r["content"])]
        view = MemoryReadView(ledger, service.store, owner)
        structured = view.assemble(raw)
        for name, query, required, forbidden in cases:
            arms = {}
            for arm, pool in (
                ("raw", raw),
                ("structured", structured),
                ("structured_controlled", structured),
                ("structured_typed_controlled", structured),
            ):
                started = time.perf_counter()
                candidates = bounded(
                    select_candidates(
                        pool, query, 8, type_recall=arm == "structured_typed_controlled"
                    )
                )
                result = (
                    await service._retrieve_qwen(
                        query, candidates, {"status": "disabled"}, 4, candidate_review=False
                    )
                    if arm in {"structured_controlled", "structured_typed_controlled"}
                    else {"evidence": candidates[:4], "status": "lexical"}
                )
                evidence = result["evidence"]
                arms[arm] = {
                    **grade(evidence, required, forbidden),
                    "evidence": evidence,
                    "status": result["status"],
                    "candidateCount": len(candidates),
                    "candidateChars": sum(len(c["content"]) for c in candidates),
                    "elapsedMs": round((time.perf_counter() - started) * 1000, 1),
                }
            rows.append(
                {
                    "case": name,
                    "query": query,
                    "required": required,
                    "forbidden": forbidden,
                    "arms": arms,
                }
            )
            print(name, {k: v["matched"] for k, v in arms.items()}, flush=True)
        report = {
            "scope": "Authored synthetic development evidence diagnostic; no independent generalization, clinical or vector superiority claim.",
            "budget": {
                "candidates": 8,
                "candidateChars": 1600,
                "deliveredEvidence": 4,
                "reranker": "disabled equally",
                "responseGenerationCalls": 0,
                "backgroundSourceReviewCalls": review_calls,
            },
            "controlledModel": args.url,
            "lifecyclePolicy": writes[0]["preview"]["policyVersion"],
            "readRevision": view.revision,
            "history": writes,
            "cases": len(rows),
            "summary": {
                arm: {
                    "matched": sum(r["arms"][arm]["matched"] for r in rows),
                    "staleOrIrrelevant": sum(r["arms"][arm]["staleOrIrrelevant"] for r in rows),
                    "fallback": sum(r["arms"][arm]["status"] == "lexical_fallback" for r in rows),
                }
                for arm in (
                    "raw",
                    "structured",
                    "structured_controlled",
                    "structured_typed_controlled",
                )
            },
            "rows": rows,
        }
        (args.out / "results.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n"
        )
        print(json.dumps(report["summary"], ensure_ascii=False), flush=True)
    finally:
        await shadow.close()
        await service.close()
        if generation:
            await generation.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", required=True)
    parser.add_argument("--key-env", default="MEMORY_API_KEY")
    parser.add_argument("--lifecycle-url", required=True)
    parser.add_argument("--lifecycle-key-env", default="MEMORY_LIFECYCLE_SHADOW_KEY")
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--fixture", type=Path)
    parser.add_argument("--source-review", action="store_true")
    parser.add_argument("--expected-sha", default="")
    asyncio.run(run(parser.parse_args()))
