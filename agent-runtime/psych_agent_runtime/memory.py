"""Independent memory control and a rebuildable, user-scoped evidence index.

Java owns source messages and a transactional outbox. This SQLite store is an
index, not the business source of truth. No generated summary replaces evidence.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx

from .bm25 import BM25
from .config import Settings
from .decision_contract import MEMORY_PAIR_CONTRACTS, memory_pair_state, profile_contract
from .decision_router import LocalDecisions
from .memory_ledger import MemoryLedger
from .memory_query import (
    asks_for_questions,
    facet_for_record,
    lookup_query,
    query_facets,
    recall_only_question,
)
from .memory_reranker import MemoryReranker
from .memory_scope import merge_sources, observation_time, recency_key, time_scope
from .memory_shadow import MemoryLifecycleShadow
from .memory_source_guard import (
    clearly_other_person,
    clearly_reverse_direction,
    reviewable_personal_source,
)
from .personal_recall import personal_recall_request

log = logging.getLogger(__name__)


class MemoryStore:
    def __init__(self, path: str):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS observations (
                    id TEXT PRIMARY KEY, owner TEXT NOT NULL, session INTEGER NOT NULL,
                    source TEXT NOT NULL, role TEXT NOT NULL, content TEXT NOT NULL,
                    observed_at TEXT NOT NULL, types TEXT NOT NULL DEFAULT '{}');
                CREATE INDEX IF NOT EXISTS memory_owner_time ON observations(owner, observed_at);
                CREATE TABLE IF NOT EXISTS relations (
                    owner TEXT NOT NULL, source TEXT NOT NULL, target TEXT NOT NULL,
                    kind TEXT NOT NULL, PRIMARY KEY(owner, source, target, kind));
            """)

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=5)
        db.row_factory = sqlite3.Row
        try:
            db.execute("PRAGMA journal_mode=WAL")
            with db:
                yield db
        finally:
            db.close()

    def ingest(self, job: dict, types: dict | None = None):
        owner = job["username"]
        with self.connect() as db:
            for role, content in [
                ("user", job["userMessage"]),
                ("assistant", job["assistantReply"]),
            ]:
                identity = hashlib.sha256(f"{owner}:{job['requestId']}:{role}".encode()).hexdigest()
                if role == "user":
                    previous = db.execute(
                        "SELECT id FROM observations WHERE owner=? AND role=? AND observed_at < ? "
                        "ORDER BY observed_at DESC, id DESC LIMIT 1",
                        (owner, role, job["createdAt"]),
                    ).fetchone()
                inserted = db.execute(
                    "INSERT OR IGNORE INTO observations "
                    "(id,owner,session,source,role,content,observed_at,types) VALUES (?,?,?,?,?,?,?,?)",
                    (
                        identity,
                        owner,
                        job["sessionId"],
                        job["requestId"],
                        role,
                        content,
                        job["createdAt"],
                        json.dumps(types or {}) if role == "user" else "{}",
                    ),
                ).rowcount
                if inserted and role == "user" and previous:
                    db.execute(
                        "INSERT OR IGNORE INTO relations VALUES (?,?,?,?)",
                        (owner, previous["id"], identity, "observed_before"),
                    )

    def candidates(
        self, owner: str, query: str, limit: int = 8, exclude_session_id=None
    ) -> list[dict]:
        # Explicit bound protects both SQL reads and the downstream model state.
        with self.connect() as db:
            rows = [
                dict(row)
                for row in db.execute(
                    "SELECT * FROM observations WHERE owner=? AND role=? AND (? IS NULL OR session!=?) "
                    "ORDER BY observed_at DESC, id DESC LIMIT 2000",
                    (owner, "user", exclude_session_id, exclude_session_id),
                )
            ]
        if not rows:
            return []
        # Filter before BM25 top-k: deduplicating questions alone still lets
        # many different paraphrases displace every actual answer source.
        if not asks_for_questions(query):
            rows = [row for row in rows if not recall_only_question(row["content"])]
        # Repeated questions must not crowd every older fact out of top-k.
        # Deduplicate only question text. Repeated real events retain their own
        # dated sources; the index itself stays untouched for audit.
        unique = {}
        for row in rows:
            key = row["content"].strip() if recall_only_question(row["content"]) else row["id"]
            unique.setdefault(key, row)
        rows = list(unique.values())
        index = BM25([row["content"] for row in rows])
        facets = query_facets(query)
        hits = index.search(query, limit)
        selected = [rows[hit.index] for hit in hits if hit.score > 0]
        if facets:
            # Reserve a small share for each requested dimension. The model still
            # decides relevance; keyword matches do not become trusted evidence.
            selected = []
            seen = set()
            for facet in facets:
                for hit in index.search(facet.terms, max(1, limit // len(facets))):
                    if hit.index not in seen:
                        selected.append(rows[hit.index])
                        seen.add(hit.index)
            for hit in hits:
                if len(selected) >= limit:
                    break
                if hit.index not in seen:
                    selected.append(rows[hit.index])
                    seen.add(hit.index)
        if not selected:
            selected = rows[: min(4, limit)]
        return self._records(selected)

    def recent(self, owner: str, limit: int = 24, exclude_session_id=None, *, query=None) -> list[dict]:
        # Semantic expansion is bounded and uses the same owner/source boundary.
        limit = max(0, min(limit, 24))
        filter_questions = query is not None and not asks_for_questions(query)
        scan = 96 if filter_questions and limit else limit
        with self.connect() as db:
            rows = [
                dict(row)
                for row in db.execute(
                    "SELECT * FROM observations WHERE owner=? AND role=? AND (? IS NULL OR session!=?) "
                    "ORDER BY observed_at DESC, id DESC LIMIT ?",
                    (owner, "user", exclude_session_id, exclude_session_id, scan),
                )
            ]
        if filter_questions:
            rows = [row for row in rows if not recall_only_question(row["content"])]
        return self._records(rows[:limit])

    @staticmethod
    def _records(rows):
        return [
            {
                "id": row["id"],
                "content": row["content"][:500],
                "truncated": len(row["content"]) > 500,
                "observedAt": row["observed_at"],
                "timestampRole": "observation_time",
                "sessionId": row["session"],
                "source": row["source"],
                "role": row["role"],
            }
            for row in rows
        ]

    def forget(self, owner: str):
        with self.connect() as db:
            db.execute("DELETE FROM relations WHERE owner=?", (owner,))
            db.execute("DELETE FROM observations WHERE owner=?", (owner,))

    def has_other_session(self, owner, session_id):
        with self.connect() as db:
            return (
                db.execute(
                    "SELECT 1 FROM observations WHERE owner=? AND role='user' AND session!=? LIMIT 1",
                    (owner, session_id),
                ).fetchone()
                is not None
            )


class MemoryService:
    def __init__(self, settings: Settings, backend=None):
        if settings.memory_provider not in {"rules", "laya", "qwen"}:
            raise ValueError("MEMORY_PROVIDER must be rules, laya or qwen")
        self.settings, self.backend = settings, backend
        self.store = MemoryStore(settings.memory_store_path)
        contract, questions = profile_contract("memory", settings.memory_contract_version)
        self.controller = (
            LocalDecisions(
                settings.memory_base_url,
                settings.memory_api_key,
                settings.memory_timeout_seconds,
                contract,
                questions,
                settings.memory_min_probability,
            )
            if settings.memory_provider in {"laya", "qwen"}
            else None
        )
        self.worker = None
        self.shadow_worker = None
        self.shadow_users = set(
            filter(None, (s.strip() for s in settings.memory_lifecycle_shadow_users.split(",")))
        )
        if "*" in self.shadow_users:
            raise ValueError("shadow memory requires explicit user allowlist")
        self.shadow = (
            MemoryLifecycleShadow(
                MemoryLedger(settings.memory_lifecycle_store_path),
                settings.memory_lifecycle_shadow_url,
                settings.memory_lifecycle_shadow_key,
            )
            if settings.memory_lifecycle_shadow_url and self.shadow_users
            else None
        )
        self.shadow_queue = asyncio.Queue(maxsize=64)
        self.shadow_stats = {"processed": 0, "dropped": 0, "failed": 0}
        self.reranker = (
            MemoryReranker(
                settings.memory_rerank_base_url,
                settings.memory_rerank_api_key,
                settings.memory_rerank_timeout_seconds,
            )
            if settings.memory_rerank_base_url
            else None
        )

    async def start(self):
        if self.backend:
            self.worker = asyncio.create_task(self._worker(), name="memory-outbox")
        if self.shadow:
            self.shadow_worker = asyncio.create_task(
                self._shadow_worker(), name="memory-lifecycle-shadow"
            )

    async def close(self):
        if self.shadow_worker:
            self.shadow_worker.cancel()
            try:
                await self.shadow_worker
            except asyncio.CancelledError:
                pass
        if self.shadow:
            await self.shadow.close()
        if self.worker:
            self.worker.cancel()
            try:
                await self.worker
            except asyncio.CancelledError:
                pass
        if self.controller:
            await self.controller.close()
        if self.reranker:
            await self.reranker.close()

    async def ingest(self, job: dict):
        types = {}
        if self.controller:
            try:
                result = await self.controller.evaluate(
                    {"observation": job["userMessage"]},
                    ["episodic", "semantic", "procedural", "preference"],
                )
                types = result["decisions"]
            except (httpx.HTTPError, ValueError, TypeError, KeyError):
                # Preserve source evidence even when model typing is unavailable.
                types = {"status": "unavailable"}
        await asyncio.to_thread(self.store.ingest, job, types)
        if self.shadow and job["username"] in self.shadow_users:
            try:
                self.shadow_queue.put_nowait(dict(job))
            except asyncio.QueueFull:
                self.shadow_stats["dropped"] += 1

    def lifecycle_info(self):
        return {
            "mode": "shadow" if self.shadow else "off",
            "formalWritesEnabled": False,
            "allowlistedUsers": len(self.shadow_users),
            "queueSize": self.shadow_queue.qsize(),
            "queueCapacity": self.shadow_queue.maxsize,
            **self.shadow_stats,
        }

    async def _shadow_worker(self):
        while True:
            job = await self.shadow_queue.get()
            try:
                await self.shadow.inspect(job)
                self.shadow_stats["processed"] += 1
            except (httpx.HTTPError, ValueError, TypeError, KeyError, sqlite3.Error):
                self.shadow_stats["failed"] += 1
                log.warning("Memory lifecycle preview failed; raw memory unchanged", exc_info=True)
            finally:
                self.shadow_queue.task_done()

    async def retrieve(
        self, owner: str, query: str, limit: int = 6, *, exclude_session_id=None,
        time_window=None, recent_messages=False, candidate_review=True,
    ) -> dict:
        query = lookup_query(query)
        scope = {"exclude_session_id": exclude_session_id} if exclude_session_id is not None else {}
        candidates = await asyncio.to_thread(self.store.candidates, owner, query, 8, **scope)
        fresh_status, fresh = "disabled", []
        if recent_messages and self.backend:
            try:
                fresh = await self.backend.recent_messages(owner)
                fresh = [c for c in fresh if c.get("role") == "user"
                         and (exclude_session_id is None or c.get("sessionId") != exclude_session_id)]
                fresh_status = "completed"
            except (httpx.HTTPError, ValueError, TypeError, KeyError):
                fresh_status = "unavailable"
        if fresh:
            candidates = merge_sources([*candidates, *fresh], self.settings.memory_legacy_timezone)[:32]
        candidates, date_scope = time_scope(candidates, time_window,
            self.settings.memory_query_timezone, self.settings.memory_legacy_timezone)
        extra_coverage = {"freshMessages": {"status": fresh_status, "count": len(fresh)}} if recent_messages else {}
        if date_scope:
            extra_coverage["timeScope"] = date_scope
        if not candidates:
            return {"evidence": [], "rounds": 0, "status": "empty", "controller": "rules",
                    "retrievalCoverage": extra_coverage}
        rerank = {"status": "disabled"}
        if self.reranker:
            try:
                pool = candidates
                if self.settings.memory_provider == "qwen":
                    recent = await asyncio.to_thread(
                        self.store.recent, owner, self.settings.memory_recall_recent_limit,
                        query=query, **scope
                    )
                    recent, _ = time_scope(recent, time_window,
                        self.settings.memory_query_timezone, self.settings.memory_legacy_timezone)
                    known = {c["id"] for c in candidates}
                    pool = merge_sources(
                        [*candidates, *(c for c in recent if c["id"] not in known)],
                        self.settings.memory_legacy_timezone,
                    )
                    seen_queries = set()
                    deduplicated = []
                    for candidate in pool:
                        if not asks_for_questions(query) and recall_only_question(candidate["content"]):
                            continue
                        if recall_only_question(candidate["content"]):
                            if candidate["content"] in seen_queries:
                                continue
                            seen_queries.add(candidate["content"])
                        deduplicated.append(candidate)
                    pool = deduplicated[:32]
                ordered, model = await self.reranker.order(query, pool)
                # Keep every original lexical candidate, add up to four semantic hits.
                known = {c["id"] for c in candidates}
                added = [c for c in ordered if c["id"] not in known][:4]
                selected = known | {c["id"] for c in added}
                candidates = [c for c in ordered if c["id"] in selected]
                rerank = {
                    "status": "completed",
                    "model": model,
                    "poolCount": len(pool),
                    "semanticAdded": len(added),
                }
            except (httpx.HTTPError, ValueError, TypeError, KeyError):
                # Ranking outage must not turn a valid original pool into no memory.
                rerank = {"status": "fallback"}
        if self.settings.memory_provider == "qwen":
            result = await self._retrieve_qwen(query, candidates, rerank, limit,
                                               temporal=bool(time_window or recent_messages),
                                               candidate_review=candidate_review)
            result["retrievalCoverage"].update(extra_coverage)
            if time_window:
                result["evidence"].sort(key=lambda r: recency_key(r, self.settings.memory_legacy_timezone), reverse=True)
            if recent_messages or time_window:
                for record in [*result["evidence"], *result.get("candidateEvidence", [])]:
                    if observed := observation_time(record, self.settings.memory_legacy_timezone):
                        record["observedAt"] = observed.astimezone(ZoneInfo(self.settings.memory_query_timezone)).isoformat()
            return result
        evidence, rounds, status = [], 0, "lexical_fallback"
        # Two bounded evidence rounds; never an autonomous Agent planning loop.
        for offset in (0, 4):
            batch = candidates[offset : offset + 4]
            if not batch:
                break
            rounds += 1
            if not self.controller:
                evidence = candidates[:limit]
                break
            try:
                if self.controller.contract in MEMORY_PAIR_CONTRACTS:
                    decisions = {}
                    for i, candidate in enumerate(batch):
                        result = await self.controller.evaluate(
                            memory_pair_state({"query": query, "candidate": candidate}),
                            ["relevance"],
                        )
                        decisions[f"relevance_{i}"] = result["decisions"]["relevance"]
                    ranked = {"decisions": decisions}
                else:
                    ranked = await self.controller.evaluate(
                        {"query": query, "candidates": batch},
                        [f"relevance_{i}" for i in range(len(batch))],
                    )
                accepted = [
                    (item["probability"], batch[i])
                    for i in range(len(batch))
                    if (item := ranked["decisions"][f"relevance_{i}"])["accepted"]
                    and item["choice"] == "yes"
                ]
                evidence.extend(item for _, item in sorted(accepted, key=lambda pair: -pair[0]))
                if rerank["status"] == "completed" and offset + 4 < len(candidates):
                    # Finish both relevance batches before judging a reordered pool.
                    status = "controlled"
                    continue
                check = await self.controller.evaluate(
                    {
                        "query": query,
                        "evidence": evidence[:8]
                        if rerank["status"] == "completed"
                        else evidence[:limit],
                    },
                    ["evidence_sufficient"],
                )
                sufficiency = check["decisions"]["evidence_sufficient"]
                status = "controlled"
                if sufficiency["accepted"] and sufficiency["choice"] == "yes":
                    status = "sufficient"
                    # A reordered first batch cannot end candidate collection.
                    if rerank["status"] != "completed":
                        break
            except (httpx.HTTPError, ValueError, TypeError, KeyError):
                evidence, status = candidates[:limit], "lexical_fallback"
                break
        if rerank["status"] == "completed" and len(evidence) > limit and status == "sufficient":
            # The response sees only the delivery limit, not all checked evidence.
            status = "controlled"
        return {
            "evidence": evidence[:limit],
            "rounds": rounds,
            "status": status,
            "controller": self.settings.memory_provider
            if status != "lexical_fallback"
            else "rules",
            "rerank": rerank,
            "retrievalCoverage": extra_coverage,
        }

    async def _retrieve_qwen(self, query, candidates, rerank, limit, *, temporal=False,
                             candidate_review=False):
        original_count = len(candidates)
        candidates = [c for c in candidates if not clearly_other_person(query, c)]
        guard = {"otherPersonBlocked": original_count - len(candidates)}
        before = len(candidates)
        candidates = [c for c in candidates if not clearly_reverse_direction(query, c)]
        guard["relationDirectionBlocked"] = before - len(candidates)
        before = len(candidates)
        # Questions about having a preference are not the preference itself.
        if not asks_for_questions(query):
            candidates = [c for c in candidates if not recall_only_question(c["content"])]
        guard["queryOnlyBlocked"] = before - len(candidates)
        evidence, status = [], "controlled"
        facets = query_facets(query)
        coverage = {"requested": [f.name for f in facets], "covered": [], "missing": []}
        facet_sources = {f.name: set() for f in facets}
        generic = bool(facets and facets[0].name == "part_1")
        try:
            if candidates:
                checked = candidates[:8] if generic else candidates
                pairs = (
                    [(c, f) for c in checked for f in facets]
                    if generic
                    else [(c, facet_for_record(facets, c["content"])) for c in checked]
                )
                states = [
                        memory_pair_state(
                            {
                                "query": facet.query if facet else query,
                                "candidate": c,
                            }
                        )
                        for c, facet in pairs
                    ]
                decisions = []
                for offset in range(0, len(states), 16):
                    decisions.extend(await self.controller.evaluate_many(states[offset:offset + 16], "relevance"))
                accepted = {}
                for (candidate, facet), decision in zip(pairs, decisions, strict=True):
                    if decision["accepted"] and decision["choice"] == "yes":
                        key = candidate["id"]
                        score = decision["probability"]
                        if key not in accepted or accepted[key][0] < score:
                            accepted[key] = (score, candidate)
                        if facet:
                            facet_sources[facet.name].add(key)
                evidence = [c for _, c in sorted(accepted.values(), key=lambda pair: -pair[0])]
                if facets:
                    # Keep at least one accepted source per dimension inside the
                    # delivery limit before filling the remaining ranked slots.
                    representatives = []
                    for facet in facets:
                        representative = next(
                            (c for c in evidence if c["id"] in facet_sources[facet.name]),
                            None,
                        )
                        if representative and representative not in representatives:
                            representatives.append(representative)
                    evidence = representatives + [c for c in evidence if c not in representatives]
            if temporal:
                evidence.sort(key=lambda r: recency_key(r, self.settings.memory_legacy_timezone), reverse=True)
            check = await self.controller.evaluate(
                {"query": query, "evidence": evidence[:8]}, ["evidence_sufficient"]
            )
            enough = check["decisions"]["evidence_sufficient"]
            # Never claim sufficiency after dropping evidence or reading a truncated source.
            if (
                evidence
                and len(evidence) <= min(limit, 8)
                and not any(c.get("truncated") for c in evidence)
                and enough["accepted"]
                and enough["choice"] == "yes"
                and (
                    not facets
                    or all(
                        facet_sources[f.name] & {c["id"] for c in evidence[:limit]} for f in facets
                    )
                )
                and (not generic or len(candidates) <= 8)
            ):
                status = "sufficient"
        except (httpx.HTTPError, ValueError, TypeError, KeyError):
            evidence, status = candidates[:limit], "lexical_fallback"
            if temporal:
                evidence.sort(key=lambda r: recency_key(r, self.settings.memory_legacy_timezone), reverse=True)
        if facets:
            coverage["covered"] = [
                f.name
                for f in facets
                if facet_sources[f.name] & {c["id"] for c in evidence[:limit]}
            ]
            coverage["missing"] = [f.name for f in facets if f.name not in coverage["covered"]]
            if generic:
                coverage["candidateLimitApplied"] = len(candidates) > 8
        pending = []
        if candidate_review and not evidence and status == "controlled" and personal_recall_request(query):
            # The final generator is already called. Let that same call review
            # at most two scoped original records; do not relabel them accepted.
            pending = [c for c in candidates if reviewable_personal_source(query, c)][:min(2, limit)]
            if pending:
                coverage["candidateReview"] = {
                    "mode": "final_response", "count": len(pending),
                    "reason": "no_model_accepted_evidence",
                }
        return {
            "evidence": evidence[:limit],
            "candidateEvidence": pending,
            "rounds": 1,
            "status": status,
            "controller": "qwen" if status != "lexical_fallback" else "rules",
            "rerank": rerank,
            "sourceGuard": guard,
            "decisionMode": "batch"
            if self.controller.batch_supported is True
            else "serial_compatibility",
            "candidateCount": len(candidates),
            "retrievalCoverage": coverage,
        }

    async def retrieve_context(self, owner, message, session_id, limit=3):
        from .context_recall import background_query

        result = await self.retrieve(
            owner, background_query(message), limit, exclude_session_id=session_id,
            recent_messages=True, candidate_review=False,
        )
        # Unchecked lexical fallback is unsuitable for unsolicited background.
        if result.get("status") == "lexical_fallback":
            result["evidence"] = []
            result["status"] = "unavailable"
        result["purpose"] = "context"
        result["scope"] = "other_sessions_same_owner"
        return result

    async def _worker(self):
        while True:
            try:
                for job in await self.backend.memory_jobs():
                    try:
                        await self.ingest(job)
                        await self.backend.complete_memory_job(job["id"])
                    except Exception:
                        log.warning("Memory job %s remains pending", job["id"], exc_info=True)
            except asyncio.CancelledError:
                raise
            except Exception:
                log.warning("Memory outbox remains pending; will resume", exc_info=True)
            await asyncio.sleep(2)
