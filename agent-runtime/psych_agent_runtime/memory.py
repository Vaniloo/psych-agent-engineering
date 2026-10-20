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
from datetime import UTC, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx

from .bm25 import BM25
from .config import Settings
from .decision_contract import MEMORY_PAIR_CONTRACTS, memory_pair_state, profile_contract
from .decision_router import LocalDecisions
from .memory_ledger import MemoryLedger
from .memory_projection import MemoryProjection
from .memory_query import (
    asks_for_questions,
    facet_for_record,
    lookup_query,
    query_facets,
    recall_only_question,
)
from .memory_read import READ_POLICY, MemoryReadView, select_candidates
from .memory_reranker import MemoryReranker
from .memory_scope import merge_sources, observation_time, recency_key, time_scope
from .memory_shadow import POLICY_VERSION, MemoryLifecycleShadow
from .memory_source_guard import (
    clearly_other_person,
    clearly_reverse_direction,
    reviewable_personal_source,
)
from .memory_update import projection_status
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
                CREATE TABLE IF NOT EXISTS memory_updates (
                    owner TEXT NOT NULL, request_id TEXT NOT NULL, result TEXT NOT NULL,
                    updated_at TEXT NOT NULL, PRIMARY KEY(owner, request_id));
                CREATE TABLE IF NOT EXISTS read_audits (
                    owner TEXT NOT NULL, request_id TEXT NOT NULL,
                    observed_at TEXT NOT NULL, metadata TEXT NOT NULL,
                    PRIMARY KEY(owner, request_id));
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

    def ingest(self, job: dict, types: dict | None = None, delivery: dict | None = None):
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
            # A reader must never see a newly indexed turn without its pending
            # receipt and mistake this brief window for an abandoned old turn.
            return self._record_receipt(db, job, delivery, initial=True) if delivery else False

    def candidates(
        self, owner: str, query: str, limit: int = 8, exclude_session_id=None, *, type_recall=False
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
        if type_recall:
            from .memory_types import diversify
            selected = diversify(rows, query, selected, limit)
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
        from .memory_types import accepted_types
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
                "memoryTypes": accepted_types(dict(row).get("types")),
            }
            for row in rows
        ]

    def forget(self, owner: str):
        with self.connect() as db:
            db.execute("DELETE FROM relations WHERE owner=?", (owner,))
            db.execute("DELETE FROM observations WHERE owner=?", (owner,))
            db.execute("DELETE FROM memory_updates WHERE owner=?", (owner,))
            db.execute("DELETE FROM read_audits WHERE owner=?", (owner,))

    def record_read_audit(self, owner, request_id, metadata):
        # Review queue, not failure gold or another transcript store.
        if not metadata.get("signals"):
            return
        with self.connect() as db:
            db.execute("INSERT OR REPLACE INTO read_audits VALUES (?,?,?,?)", (
                owner, request_id, datetime.now(UTC).isoformat(), json.dumps(metadata)))
            db.execute("DELETE FROM read_audits WHERE julianday(observed_at) < julianday('now','-14 days')")
            db.execute("DELETE FROM read_audits WHERE owner=? AND request_id NOT IN "
                       "(SELECT request_id FROM read_audits WHERE owner=? ORDER BY observed_at DESC LIMIT 200)",
                       (owner, owner))

    def update_receipt(self, job, result):
        with self.connect() as db:
            self._record_receipt(db, job, result)

    @staticmethod
    def _record_receipt(db, job, result, *, initial=False):
        conflict = "DO NOTHING" if initial else "DO UPDATE SET result=excluded.result,updated_at=excluded.updated_at"
        inserted = db.execute(
            "INSERT INTO memory_updates VALUES (?,?,?,?) ON CONFLICT(owner,request_id) " + conflict,
            (job["username"], job["requestId"], json.dumps(result), datetime.now(UTC).isoformat()),
        ).rowcount
        db.execute("DELETE FROM memory_updates WHERE owner=? AND request_id NOT IN "
                   "(SELECT request_id FROM memory_updates WHERE owner=? ORDER BY updated_at DESC LIMIT 200)",
                   (job["username"], job["username"]))
        return bool(inserted)

    def update_status(self, owner, request_id):
        with self.connect() as db:
            row = db.execute("SELECT * FROM memory_updates WHERE owner=? AND request_id=?",
                             (owner, request_id)).fetchone()
            if row:
                result = {**json.loads(row["result"]), "recentReady": True,
                          "updatedAt": row["updated_at"]}
                if (result["status"] == "pending" and
                        (datetime.now(UTC) - datetime.fromisoformat(row["updated_at"])).total_seconds() > 45):
                    result["status"] = "unconfirmed"
                return result
            return None

    def delivery_status(self, owner, request_id):
        if result := self.update_status(owner, request_id):
            return result
        with self.connect() as db:
            ready = db.execute("SELECT 1 FROM observations WHERE owner=? AND source=? AND role='user'",
                               (owner, request_id)).fetchone() is not None
        return {"status": "unconfirmed" if ready else "pending", "recentReady": ready}

    def source_rows(self, owner, requests):
        """Exact owned source hydration, including text beyond the retrieval cap."""
        result = {}
        requests = sorted(requests)
        with self.connect() as db:
            for offset in range(0, len(requests), 500):
                chunk = requests[offset:offset + 500]
                for row in db.execute(
                    "SELECT * FROM observations WHERE owner=? AND role='user' AND source IN ("
                    + ",".join("?" for _ in chunk) + ")", (owner, *chunk),
                ):
                    record = self._records([row])[0]
                    record.update(content=row["content"], truncated=False)
                    result[row["source"]] = record
        return result

    def read_pool(self, owner):
        with self.connect() as db:
            return self._records(db.execute(
                "SELECT * FROM observations WHERE owner=? AND role='user' "
                "ORDER BY observed_at DESC,id DESC LIMIT 2000", (owner,),
            ).fetchall())

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
        self.sandbox_users = set(
            filter(None, (s.strip() for s in settings.memory_lifecycle_sandbox_users.split(",")))
        )
        if self.sandbox_users and (
            "*" in self.sandbox_users
            or not self.sandbox_users <= self.shadow_users
            or not settings.memory_lifecycle_shadow_url
        ):
            raise ValueError("sandbox users must be an explicit subset of enabled shadow users")
        if (settings.memory_lifecycle_shadow_url and self.shadow_users
                and Path(settings.memory_lifecycle_store_path).resolve() == self.store.path.resolve()):
            raise ValueError("experimental ledger must be separate from raw memory index")
        from .ollama_client import OllamaClient
        self.source_reviewer = (OllamaClient(settings) if settings.memory_source_review_enabled
            and settings.memory_lifecycle_shadow_url and self.shadow_users else None)
        self.shadow = (
            MemoryLifecycleShadow(
                MemoryLedger(settings.memory_lifecycle_store_path),
                settings.memory_lifecycle_shadow_url,
                settings.memory_lifecycle_shadow_key,
                expected_sha=settings.memory_lifecycle_expected_sha,
                source_reviewer=self.source_reviewer, review_model=settings.generation_model,
                review_provider=settings.ai_provider,
            )
            if settings.memory_lifecycle_shadow_url and self.shadow_users
            else None
        )
        self.shadow_queue = asyncio.Queue(maxsize=64)
        self.shadow_stats = {"processed": 0, "dropped": 0, "failed": 0}
        self.projection = (
            MemoryProjection(self.shadow.ledger, self.sandbox_users,
                             require_source_review=settings.memory_source_review_enabled)
            if self.shadow and self.sandbox_users else None
        )
        self.projection_stats = {"appliedChanges": 0, "notApplied": 0}
        self.read_users = set(filter(None, (
            s.strip() for s in settings.memory_lifecycle_read_users.split(",")
        )))
        if self.read_users and ("*" in self.read_users or not self.read_users <= self.sandbox_users):
            raise ValueError("lifecycle readers must be an explicit subset of sandbox users")
        self.active_users = set(filter(None, (s.strip() for s in settings.memory_lifecycle_active_users.split(","))))
        if self.active_users and ("*" in self.active_users or not self.active_users <= self.read_users
                                  or not settings.memory_lifecycle_expected_sha or self.source_reviewer is None):
            raise ValueError("active memory requires explicit readers, a pinned artifact and source review")
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
        if self.source_reviewer:
            await self.source_reviewer.close()
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
        tracking = self.projection is not None and job["username"] in self.sandbox_users
        inserted = await asyncio.to_thread(self.store.ingest, job, types,
                                          {"status": "pending" if tracking else "indexed"})
        if not inserted:
            return  # outbox acknowledgement replay must not reset a completed receipt
        if self.shadow and job["username"] in self.shadow_users:
            try:
                self.shadow_queue.put_nowait(dict(job))
            except asyncio.QueueFull:
                self.shadow_stats["dropped"] += 1
                if tracking:
                    await asyncio.to_thread(self.store.update_receipt, job, {"status": "unconfirmed"})

    def lifecycle_info(self):
        return {
            "mode": "controlled" if self.active_users else "shadow" if self.shadow else "off",
            "policyVersion": POLICY_VERSION if self.shadow else None,
            "formalWritesEnabled": bool(self.active_users),
            "activeUsers": len(self.active_users),
            "writeScope": "explicit_allowlist",
            "sourceAssertionReview": self.source_reviewer is not None,
            "read": {"mode": "controlled" if self.active_users else "sandbox" if self.read_users else "off",
                     "policyVersion": READ_POLICY, "allowlistedUsers": len(self.read_users)},
            "projection": {
                "mode": "controlled" if self.active_users else "sandbox" if self.projection else "off",
                "allowlistedUsers": len(self.sandbox_users),
                **self.projection_stats,
            },
            "allowlistedUsers": len(self.shadow_users),
            "queueSize": self.shadow_queue.qsize(),
            "queueCapacity": self.shadow_queue.maxsize,
            **self.shadow_stats,
        }

    async def _shadow_worker(self):
        while True:
            job = await self.shadow_queue.get()
            try:
                result = await self.shadow.inspect(job)
                self.shadow_stats["processed"] += 1
                if any(p.get("previewError") for p in result.get("proposals", [])):
                    self.shadow_stats["failed"] += 1
                if self.projection:
                    projected = await asyncio.to_thread(self.projection.project, job, result)
                    self.projection_stats["appliedChanges"] += sum(
                        p.get("applied") is True for p in projected
                    )
                    self.projection_stats["notApplied"] += sum(
                        p.get("applied") is not True for p in projected
                    )
                    if job["username"] in self.sandbox_users:
                        await asyncio.to_thread(self.store.update_receipt, job,
                                                projection_status(result, projected))
            except (httpx.HTTPError, ValueError, TypeError, KeyError, sqlite3.Error):
                self.shadow_stats["failed"] += 1
                if job["username"] in self.sandbox_users:
                    try:
                        await asyncio.to_thread(self.store.update_receipt, job,
                                                {"status": "unconfirmed"})
                    except sqlite3.Error:
                        log.warning("Memory delivery receipt unavailable", exc_info=True)
                log.warning("Memory lifecycle preview failed; raw memory unchanged", exc_info=True)
            finally:
                self.shadow_queue.task_done()

    async def retrieve(
        self, owner: str, query: str, limit: int = 6, *, exclude_session_id=None,
        time_window=None, recent_messages=False, candidate_review=True,
    ) -> dict:
        view = None
        try:
            if owner in self.read_users:
                view = await asyncio.to_thread(
                    MemoryReadView, self.shadow.ledger, self.store, owner,
                    include_questions=asks_for_questions(query),
                )
            result = await self._retrieve(
                owner, query, limit, exclude_session_id=exclude_session_id,
                time_window=time_window, recent_messages=recent_messages,
                candidate_review=candidate_review, view=view,
            )
            if view:
                if not await asyncio.to_thread(view.unchanged):
                    raise ValueError("lifecycle changed during retrieval")
                result.setdefault("retrievalCoverage", {})["lifecycleRead"] = {
                    "mode": "controlled" if owner in self.active_users else "sandbox", "policyVersion": READ_POLICY,
                    "revision": view.revision, "activeFacts": len(view.facts),
                    "scope": "current_facts_and_unmanaged_raw",
                }
            return result
        except (ValueError, sqlite3.Error):
            if owner not in self.read_users:
                raise
            # Never fall back to unmasked raw after a source/revision failure.
            log.warning("Experimental memory read unavailable", exc_info=True)
            return {"evidence": [], "candidateEvidence": [], "rounds": 0,
                    "status": "unavailable", "controller": "rules",
                    "retrievalCoverage": {"lifecycleRead": {
                        "mode": "sandbox", "policyVersion": READ_POLICY,
                        "status": "source_or_revision_unavailable"}}}

    async def _retrieve(
        self, owner, query, limit, *, exclude_session_id=None, time_window=None,
        recent_messages=False, candidate_review=True, view=None,
    ):
        query = lookup_query(query)
        scope = {"exclude_session_id": exclude_session_id} if exclude_session_id is not None else {}
        candidates = await asyncio.to_thread(self.store.candidates, owner, query, 8,
            **({"type_recall": True} if self.settings.memory_type_recall_enabled else {}), **scope)
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
        view_pool = None
        if view:
            raw = await asyncio.to_thread(self.store.read_pool, owner)
            view_pool = view.assemble([*raw, *fresh], exclude_session_id=exclude_session_id)
            if not asks_for_questions(query):
                view_pool = [c for c in view_pool if not recall_only_question(c["content"])]
            view_pool, _ = time_scope(view_pool, time_window,
                self.settings.memory_query_timezone, self.settings.memory_legacy_timezone)
            candidates = select_candidates(view_pool, query,
                type_recall=self.settings.memory_type_recall_enabled)
        candidates, date_scope = time_scope(candidates, time_window,
            self.settings.memory_query_timezone, self.settings.memory_legacy_timezone)
        extra_coverage = {"freshMessages": {"status": fresh_status, "count": len(fresh)}} if recent_messages else {}
        if self.settings.memory_type_recall_enabled:
            from .memory_types import POLICY, TYPES
            extra_coverage["typeRecall"] = {"policy": POLICY, "queryClassifier": False,
                "mode": "soft_diversity", "baselineReservedFraction": .75,
                "candidateTypes": {name: sum(name in c.get("memoryTypes", []) for c in candidates)
                                   for name in TYPES}}
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
                    recent = view_pool[:self.settings.memory_recall_recent_limit] if view else await asyncio.to_thread(
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
        recap = personal_recall_request(query)
        broad_recap = recap and recap["topic"] in {"personal_overview", "recent_conversations"}
        if candidate_review and recap and (not evidence or broad_recap) and status == "controlled":
            # The final generator is already called. Let that same call review
            # at most two scoped original records; do not relabel them accepted.
            accepted_ids = {c["id"] for c in evidence}
            pending = [c for c in candidates if c["id"] not in accepted_ids
                       and reviewable_personal_source(query, c)][:min(2, limit)]
            if pending:
                coverage["candidateReview"] = {
                    "mode": "final_response", "count": len(pending),
                    "reason": "bounded_recap_coverage" if evidence else "no_model_accepted_evidence",
                }
        audit = {"candidateSourceIds": [c["id"] for c in candidates],
                 "acceptedSourceIds": [c["id"] for c in evidence[:limit]],
                 "reviewSourceIds": [c["id"] for c in pending],
                 "acceptedBeforeDelivery": len(evidence), "deliveryLimit": limit}
        return {
            "evidence": evidence[:limit],
            "candidateEvidence": pending,
            "audit": audit,
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
