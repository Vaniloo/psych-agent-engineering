"""User-scoped evidence index for cross-session recall.

Java owns source messages and a transactional outbox. This SQLite store is a
rebuildable index, not the business source of truth: the background worker reads
outbox jobs, indexes each turn's original text, then acknowledges the job.
Retrieval is BM25 over the owner's own messages, optionally merged with the
latest messages from Java to cover indexing delay, then scoped by time.
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

import httpx

from .bm25 import BM25
from .config import Settings
from .memory_query import asks_for_questions, lookup_query, query_facets, recall_only_question
from .memory_scope import merge_sources, time_scope

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

    def ingest(self, job: dict, delivery: dict | None = None):
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
                        "{}",
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
        self.settings, self.backend = settings, backend
        self.store = MemoryStore(settings.memory_store_path)
        self.worker = None

    async def start(self):
        if self.backend:
            self.worker = asyncio.create_task(self._worker(), name="memory-outbox")

    async def close(self):
        if self.worker:
            self.worker.cancel()
            try:
                await self.worker
            except asyncio.CancelledError:
                pass

    async def ingest(self, job: dict):
        # Replayed outbox acknowledgements must not reset a completed receipt.
        await asyncio.to_thread(self.store.ingest, job, {"status": "indexed"})

    async def retrieve(
        self, owner: str, query: str, limit: int = 6, *, exclude_session_id=None,
        time_window=None, recent_messages=False,
    ) -> dict:
        query = lookup_query(query)
        candidates = await asyncio.to_thread(
            self.store.candidates, owner, query, 8, exclude_session_id
        )
        coverage = {}
        if recent_messages and self.backend:
            # The index lags the outbox by a few seconds; merge Java's latest rows.
            fresh_status, fresh = "unavailable", []
            try:
                fresh = [
                    c for c in await self.backend.recent_messages(owner)
                    if c.get("role") == "user"
                    and (exclude_session_id is None or c.get("sessionId") != exclude_session_id)
                ]
                fresh_status = "completed"
            except (httpx.HTTPError, ValueError, TypeError, KeyError):
                pass
            if fresh:
                candidates = merge_sources(
                    [*candidates, *fresh], self.settings.memory_legacy_timezone
                )[:32]
            coverage["freshMessages"] = {"status": fresh_status, "count": len(fresh)}
        candidates, date_scope = time_scope(
            candidates, time_window,
            self.settings.memory_query_timezone, self.settings.memory_legacy_timezone,
        )
        if date_scope:
            coverage["timeScope"] = date_scope
        if not candidates:
            return {"evidence": [], "rounds": 0, "status": "empty", "controller": "rules",
                    "retrievalCoverage": coverage}
        # Lexical candidates are delivered as-is; the response step cites their sources.
        return {"evidence": candidates[:limit], "rounds": 1, "status": "lexical_fallback",
                "controller": "rules", "rerank": {"status": "disabled"},
                "retrievalCoverage": coverage}

    async def retrieve_context(self, owner, message, session_id, limit=3):
        from .context_recall import background_query

        result = await self.retrieve(
            owner, background_query(message), limit, exclude_session_id=session_id,
            recent_messages=True,
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
