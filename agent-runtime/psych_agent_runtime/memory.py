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

import httpx

from .bm25 import BM25
from .config import Settings
from .decision_contract import MEMORY_PAIR_CONTRACT, memory_pair_state, profile_contract
from .decision_router import LocalDecisions

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

    def candidates(self, owner: str, query: str, limit: int = 8) -> list[dict]:
        # Explicit bound protects both SQL reads and the downstream model state.
        with self.connect() as db:
            rows = [
                dict(row)
                for row in db.execute(
                    "SELECT * FROM observations WHERE owner=? AND role=? "
                    "ORDER BY observed_at DESC, id DESC LIMIT 2000",
                    (owner, "user"),
                )
            ]
        if not rows:
            return []
        hits = BM25([row["content"] for row in rows]).search(query, limit)
        selected = [rows[hit.index] for hit in hits if hit.score > 0]
        if not selected:
            selected = rows[: min(4, limit)]
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
            for row in selected
        ]

    def forget(self, owner: str):
        with self.connect() as db:
            db.execute("DELETE FROM relations WHERE owner=?", (owner,))
            db.execute("DELETE FROM observations WHERE owner=?", (owner,))


class MemoryService:
    def __init__(self, settings: Settings, backend=None):
        if settings.memory_provider not in {"rules", "laya"}:
            raise ValueError("MEMORY_PROVIDER must be rules or laya")
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
            if settings.memory_provider == "laya"
            else None
        )
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
        if self.controller:
            await self.controller.close()

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

    async def retrieve(self, owner: str, query: str, limit: int = 6) -> dict:
        candidates = await asyncio.to_thread(self.store.candidates, owner, query, 8)
        if not candidates:
            return {"evidence": [], "rounds": 0, "status": "empty", "controller": "rules"}
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
                if self.controller.contract == MEMORY_PAIR_CONTRACT:
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
                check = await self.controller.evaluate(
                    {"query": query, "evidence": evidence[:limit]}, ["evidence_sufficient"]
                )
                sufficiency = check["decisions"]["evidence_sufficient"]
                status = "controlled"
                if sufficiency["accepted"] and sufficiency["choice"] == "yes":
                    status = "sufficient"
                    break
            except (httpx.HTTPError, ValueError, TypeError, KeyError):
                evidence, status = candidates[:limit], "lexical_fallback"
                break
        return {
            "evidence": evidence[:limit],
            "rounds": rounds,
            "status": status,
            "controller": "laya" if status != "lexical_fallback" else "rules",
        }

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
