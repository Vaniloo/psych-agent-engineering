"""Opt-in lifecycle preview. No call to apply; no changes to chat memory retrieval."""

import re

import httpx

from .bm25 import BM25
from .decision_contract import binary
from .decision_router import LocalDecisions
from .memory_ledger import proposal
from .memory_lifecycle_contract import CONTRACT, LIFECYCLE_INSTRUCTIONS
from .memory_query import recall_only_question


class MemoryLifecycleShadow:
    def __init__(self, ledger, url, key, *, timeout=5):
        self.ledger = ledger
        self.controller = LocalDecisions(
            url,
            key,
            timeout,
            CONTRACT,
            {k: binary(v) for k, v in LIFECYCLE_INSTRUCTIONS.items()},
            0.85,
        )

    async def close(self):
        await self.controller.close()

    async def inspect(self, job):
        text = job["userMessage"]
        if not text or len(text) > 2000:
            return {"status": "skipped", "reason": "source_budget", "proposals": []}
        if re.search(r"(?:不要|别|不用|不想).{0,8}(?:长期)?(?:记住|保存|存储)|忘掉|忘记这", text):
            return {"status": "skipped", "reason": "retention_optout", "proposals": []}
        results = []
        # No paraphrasing or partial truncation; complete source remains visible
        # to the controller, even when a later clause corrects an earlier one.
        for match in re.finditer(r"[^。！？\n]+[。！？]?", text):
            clause = match.group()
            if (
                len(clause) > 400
                or recall_only_question(clause)
                or not re.search(r"我|自己", clause)
                or not clause.strip()
            ):
                continue
            scope = (
                "temporary"
                if re.search(r"今天|昨天|刚才|此刻|这次|最近|这几天", clause)
                else "stable"
            )
            item = proposal(job, match.start(), match.end(), scope=scope)
            state = {
                "source": {"role": "user", "content": text},
                "candidate": {"content": clause, "scope": scope},
            }
            target = None
            model = "unavailable"
            decisions = {}
            try:
                answer = await self.controller.evaluate(state, ["write_worthy", "grounded"])
                decisions.update(answer["decisions"])
                model = answer["model"]
                # All candidate objects and IDs come from the owned ledger.
                eligible = all(v["accepted"] and v["choice"] == "yes" for v in decisions.values())
                existing = self.ledger.candidates(job["username"]) if eligible else []
                ranked = BM25([r["content"] for r in existing]).search(clause, 3)
                for hit in ranked:
                    candidate = existing[hit.index]
                    pair = await self.controller.evaluate(
                        {
                            **state,
                            "existing": {
                                "content": candidate["content"],
                                "scope": candidate["scope"],
                            },
                        },
                        ["duplicate", "supersedes", "conflict"],
                    )
                    decisions.update(pair["decisions"])
                    target = candidate
                    # First relevant pair determines a preview, never a write.
                    if any(
                        v["accepted"] and v["choice"] == "yes" for v in pair["decisions"].values()
                    ):
                        break
            except (httpx.HTTPError, ValueError, TypeError, KeyError) as exc:
                # A preview failure neither fails raw ingestion nor authorizes writes.
                decisions = {"preview_error": type(exc).__name__}
            action = self.ledger.record_shadow(item, decisions, target, model=model)
            results.append(
                {
                    "proposalId": item["id"],
                    "action": action,
                    "targetId": target["id"] if target else None,
                    "model": model,
                    "decisions": decisions,
                }
            )
            if len(results) == 2:
                break
        return {"status": "shadow_only", "applied": False, "proposals": results}
