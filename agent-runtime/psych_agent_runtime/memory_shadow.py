"""Opt-in lifecycle preview. No call to apply; no changes to chat memory retrieval."""

import re

import httpx

from .bm25 import BM25
from .decision_contract import binary
from .decision_router import LocalDecisions
from .memory_facts import attribute_relation, fact_spans, target_verified
from .memory_ledger import choose_action, proposal
from .memory_lifecycle_contract import CONTRACT, LIFECYCLE_INSTRUCTIONS
from .memory_query import recall_only_question

POLICY_VERSION = "memory-lifecycle-shadow-v3"


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
        revision = self.ledger.revision(job["username"])
        spans = [s for s in fact_spans(text) if not recall_only_question(text[s[0] : s[1]])]
        # No paraphrasing or partial truncation; complete source remains visible
        # to the controller, even when a later clause corrects an earlier one.
        for start, end, scope in spans[:2]:
            clause = text[start:end]
            item = proposal(job, start, end, scope=scope)
            state = {
                "source": {"role": "user", "content": text},
                "candidate": {"content": clause, "scope": scope},
            }
            target = None
            model = "unavailable"
            decisions = {}
            comparisons = []
            review_reason = preview_error = None
            excluded_attributes = 0
            try:
                if clause == text:
                    answer = await self.controller.evaluate(state, ["write_worthy", "grounded"])
                else:
                    # Value applies to this exact span; grounding sees the full
                    # message so quotations/corrections are not silently cut out.
                    answer = await self.controller.evaluate(
                        {"source": {"role": "user", "content": clause}}, ["write_worthy"]
                    )
                    grounded = await self.controller.evaluate(state, ["grounded"])
                    if grounded["model"] != answer["model"]:
                        raise ValueError("controller changed during extraction")
                    answer["decisions"].update(grounded["decisions"])
                decisions.update(answer["decisions"])
                model = answer["model"]
                # All candidate objects and IDs come from the owned ledger.
                eligible = all(v["accepted"] and v["choice"] == "yes" for v in decisions.values())
                existing = self.ledger.candidates(job["username"], limit=200) if eligible else []
                compatible = [
                    c for c in existing if attribute_relation(clause, c["content"]) != "different"
                ]
                excluded_attributes = len(existing) - len(compatible)
                existing = compatible
                ranked = BM25([r["content"] for r in existing]).search(clause, 3)
                pairs = []
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
                    if pair["model"] != model:
                        raise ValueError("controller changed during preview")
                    combined = {**decisions, **pair["decisions"]}
                    action = choose_action(combined, True)
                    boundary = attribute_relation(clause, candidate["content"])
                    if not target_verified(action, clause, candidate["content"]):
                        action = "review"
                    pairs.append((candidate, combined, action))
                    comparisons.append(
                        {
                            "targetId": candidate["id"],
                            "targetVersion": candidate["version"],
                            "action": action,
                            "attributeBoundary": boundary,
                            "decisions": pair["decisions"],
                        }
                    )
                # An unrelated later pair cannot erase an earlier abstention.
                # Multiple relationships need reconciliation, not a first-hit write.
                uncertain = [p for p in pairs if p[2] == "review"]
                related = [p for p in pairs if p[2] not in {"add", "review"}]
                if uncertain:
                    target, decisions, _ = uncertain[0]
                    review_reason = "candidate_requires_review"
                elif len(related) > 1:
                    review_reason = "multiple_related_targets"
                elif related:
                    target, decisions, _ = related[0]
            except (httpx.HTTPError, ValueError, TypeError, KeyError) as exc:
                # A preview failure neither fails raw ingestion nor authorizes writes.
                preview_error = type(exc).__name__
                review_reason = "preview_unavailable"
            action = self.ledger.record_shadow(
                item,
                decisions,
                target,
                model=model,
                review_reason=review_reason,
                comparisons=comparisons,
                policy_version=POLICY_VERSION,
                ledger_revision=revision,
            )
            results.append(
                {
                    "proposalId": item["id"],
                    "sourceSpan": {"start": start, "end": end, "scope": scope},
                    "action": action,
                    "targetId": target["id"] if target else None,
                    "model": model,
                    "decisions": decisions,
                    "reviewReason": review_reason,
                    "previewError": preview_error,
                    "comparisons": comparisons,
                    "excludedAttributes": excluded_attributes,
                }
            )
            if len(results) == 2:
                break
        return {
            "status": "shadow_only",
            "policyVersion": POLICY_VERSION,
            "ledgerRevision": revision,
            "omittedCandidates": max(0, len(spans) - 2),
            "applied": False,
            "proposals": results,
        }
