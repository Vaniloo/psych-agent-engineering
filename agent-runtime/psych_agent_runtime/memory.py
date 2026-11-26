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
import math
import sqlite3
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

import httpx

from .bm25 import BM25
from .config import Settings
from .memory_scope import time_scope

log = logging.getLogger(__name__)


_RRF_K = 60


def _cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    norm = math.sqrt(sum(x * x for x in a)) * math.sqrt(sum(y * y for y in b))
    return dot / norm if norm else 0.0


def rank(query: str, pool: list[dict], vector: list[float] | None, depth: int = 8,
         min_similarity: float = 0.35) -> list[dict]:
    """Fuse a BM25 ranking and a cosine ranking with reciprocal rank fusion.

    The same sentence said many times keeps only its newest copy, so a repeated
    question cannot crowd out the one message that actually answers it.
    """
    unique: dict[str, dict] = {}
    for record in pool:  # pool is newest first
        unique.setdefault(record["content"].strip(), record)
    records = list(unique.values())
    scores: dict[int, float] = {}
    for position, hit in enumerate(BM25([r["content"] for r in records]).search(query, depth), start=1):
        scores[hit.index] = scores.get(hit.index, 0.0) + 1 / (_RRF_K + position)
    if vector is not None:
        similar = sorted(((i, _cosine(vector, r["embedding"])) for i, r in enumerate(records)
                          if r.get("embedding")), key=lambda pair: pair[1], reverse=True)
        for position, (i, similarity) in enumerate(similar[:depth], start=1):
            if similarity >= min_similarity:
                scores[i] = scores.get(i, 0.0) + 1 / (_RRF_K + position)
    return [records[i] for i, _ in sorted(scores.items(), key=lambda pair: pair[1], reverse=True)]


def strip(record: dict) -> dict:
    return {k: v for k, v in record.items() if k != "embedding"}


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
            columns = {row["name"] for row in db.execute("PRAGMA table_info(observations)")}
            if "embedding" not in columns:
                db.execute("ALTER TABLE observations ADD COLUMN embedding TEXT")

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

    def ingest(self, job: dict, delivery: dict | None = None, embedding: list[float] | None = None):
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
                    "(id,owner,session,source,role,content,observed_at,types,embedding) VALUES (?,?,?,?,?,?,?,?,?)",
                    (
                        identity,
                        owner,
                        job["sessionId"],
                        job["requestId"],
                        role,
                        content,
                        job["createdAt"],
                        "{}",
                        json.dumps(embedding) if role == "user" and embedding else None,
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

    def rows(self, owner: str, exclude_session_id=None, limit: int = 2000) -> list[dict]:
        """The owner's own user messages, newest first, with stored embeddings."""
        with self.connect() as db:
            rows = db.execute(
                "SELECT * FROM observations WHERE owner=? AND role='user' AND (? IS NULL OR session!=?) "
                "ORDER BY observed_at DESC, id DESC LIMIT ?",
                (owner, exclude_session_id, exclude_session_id, limit)).fetchall()
        records = self._records(rows)
        for record, row in zip(records, rows, strict=True):
            record["embedding"] = json.loads(row["embedding"]) if row["embedding"] else None
        return records

    def candidates(self, owner: str, query: str, limit: int = 8, exclude_session_id=None) -> list[dict]:
        """Lexical-only lookup (no embedder needed); used by tests and offline tools."""
        pool = self.rows(owner, exclude_session_id)
        ranked = rank(query, pool, None)
        return [strip(r) for r in (ranked or pool[: min(4, limit)])[:limit]]

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
    def __init__(self, settings: Settings, backend=None, writer=None, embedder=None):
        self.settings, self.backend, self.writer, self.embedder = settings, backend, writer, embedder
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
        embedding = await self._embed(job["userMessage"])
        # Replayed outbox acknowledgements must not reset a completed receipt.
        await asyncio.to_thread(self.store.ingest, job, {"status": "indexed"}, embedding)

    async def _embed(self, text: str) -> list[float] | None:
        if self.embedder is None:
            return None
        try:
            return (await self.embedder.embed([text[:2000]], purpose="memory_embedding"))[0]
        except Exception:  # noqa: BLE001 - lexical recall still works without a vector
            log.warning("Memory embedding unavailable; record indexed for BM25 only")
            return None

    async def retrieve(
        self, owner: str, query: str, limit: int = 6, *, exclude_session_id=None,
        time_window=None, recent_messages=False,
    ) -> dict:
        """Hybrid recall over the owner's own words: BM25 + vector similarity, fused by rank."""
        pool = await asyncio.to_thread(self.store.rows, owner, exclude_session_id)
        coverage = {"indexed": len(pool)}
        if recent_messages and self.backend:
            # The index lags the outbox by a few seconds; add Java's latest rows it does not have yet.
            try:
                known = {(r["sessionId"], r["content"]) for r in pool}
                fresh = [c for c in await self.backend.recent_messages(owner)
                         if c.get("role") == "user"
                         and (exclude_session_id is None or c.get("sessionId") != exclude_session_id)
                         and (c.get("sessionId"), c.get("content")) not in known]
                pool = [*fresh, *pool]
                coverage["freshMessages"] = len(fresh)
            except (httpx.HTTPError, ValueError, TypeError, KeyError):
                coverage["freshMessages"] = "unavailable"
        pool, date_scope = time_scope(pool, time_window, self.settings.memory_query_timezone,
                                      self.settings.memory_legacy_timezone)
        if date_scope:
            coverage["timeScope"] = date_scope
        if not pool:
            return {"evidence": [], "status": "empty", "retrievalCoverage": coverage}
        vector = await self._embed(query)
        ranked = rank(query, pool, vector)
        coverage["vector"] = vector is not None
        if ranked:
            return {"evidence": [strip(r) for r in ranked[:limit]], "status": "ranked",
                    "retrievalCoverage": coverage}
        # Nothing matched either route: show the latest records and let the model judge.
        return {"evidence": [strip(r) for r in pool[: min(4, limit)]], "status": "recent_only",
                "retrievalCoverage": coverage}

    async def _worker(self):
        while True:
            try:
                for job in await self.backend.memory_jobs():
                    try:
                        await self.ingest(job)
                        # Index first (cheap, always), then fold the turn into the summaries.
                        update = await self.writer.summarize(job) if self.writer else None
                        await self.backend.complete_memory_job(job["id"], update)
                    except Exception:
                        log.warning("Memory job %s remains pending", job["id"], exc_info=True)
            except asyncio.CancelledError:
                raise
            except Exception:
                log.warning("Memory outbox remains pending; will resume", exc_info=True)
            await asyncio.sleep(2)
