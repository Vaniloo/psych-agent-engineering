from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

from redis.exceptions import RedisError

from .bm25 import BM25
from .chunking import TokenAwareChunker
from .config import Settings
from .ollama_client import OllamaClient
from .schemas import (
    KnowledgeUpsertRequest,
    RagHit,
    RagSearchMetrics,
    RagSearchResult,
)
from .telemetry import current_run

log = logging.getLogger(__name__)

_RETRIEVAL_SCHEMA_VERSION = "hybrid-rrf-v8"
_RRF_K = 60


@dataclass
class _SparseIndex:
    """BM25 over every chunk, kept in memory until the collection changes."""
    signature: tuple
    ids: list[str]
    documents: list[str]
    metadatas: list[dict]
    bm25: BM25


class RagEngine:
    """Hybrid retriever with Chroma as the durable vector database."""

    def __init__(self, settings: Settings, ollama: OllamaClient) -> None:
        self.settings = settings
        self.ollama = ollama
        self.chunker = TokenAwareChunker()
        self._collection: Any = None
        self._redis: Any = None
        self._connect_lock = asyncio.Lock()
        self._sparse: _SparseIndex | None = None
        self._sparse_lock = asyncio.Lock()

    async def start(self) -> None:
        await self._ensure_collection()
        await self._ensure_redis()

    async def close(self) -> None:
        if self._redis is not None:
            await self._redis.aclose()

    async def health(self) -> dict[str, bool]:
        chroma_ok = False
        try:
            collection = await self._ensure_collection()
            await asyncio.to_thread(collection.count)
            chroma_ok = True
        except Exception:
            log.exception("Chroma health check failed")
        return {"chroma": chroma_ok, "ollama": await self.ollama.health()}

    async def knowledge_info(self) -> dict[str, Any]:
        try:
            async with asyncio.timeout(2):
                collection = await self._ensure_collection()
                count = await asyncio.to_thread(collection.count)
            return {
                "collection": self.settings.chroma_collection,
                "chunks": count,
                "status": "ready" if count else "empty",
            }
        except Exception:
            log.warning("Knowledge readiness unavailable", exc_info=True)
            return {
                "collection": self.settings.chroma_collection,
                "chunks": None,
                "status": "unavailable",
            }

    async def upsert_document(self, request: KnowledgeUpsertRequest) -> int:
        prefix = "\n".join(request.metadata.get(k, "") for k in ("audience", "title", "keywords", "section")).strip()
        if prefix:
            prefix = self.chunker.estimator.truncate(prefix, min(96, request.chunk_size // 3)) + "\n"
        prefix_tokens = self.chunker.estimator.count(prefix) if prefix else 0
        chunks = self.chunker.split(request.content, request.chunk_size - prefix_tokens, request.overlap)
        texts = [prefix + chunk.text for chunk in chunks]
        revision = hashlib.sha256(json.dumps({
            "chunk_size": request.chunk_size, "overlap": request.overlap,
            "content": request.content, "metadata": request.metadata,
            "source": request.source, "category": request.category,
            "embedding_model": self.settings.embedding_model,
        }, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
        collection = await self._ensure_collection()
        existing = await asyncio.to_thread(collection.get, where={"$and": [
            {"document_id": request.document_id}, {"revision": revision}
        ]}, include=["metadatas"])
        if len(existing.get("ids", [])) == len(chunks):
            return len(chunks)
        embeddings: list[list[float]] = []
        batch_size = max(1, self.settings.embedding_batch_size)
        for start in range(0, len(chunks), batch_size):
            batch = chunks[start : start + batch_size]
            embeddings.extend(
                await self.ollama.embed(
                    texts[start : start + len(batch)],
                    purpose="knowledge_index_embedding",
                )
            )
        ids = [f"{request.document_id}:{revision[:12]}:chunk:{chunk.index}" for chunk in chunks]
        metadatas = [
            {
                "document_id": request.document_id,
                "chunk_index": chunk.index,
                "category": request.category,
                "source": request.source,
                "revision": revision,
                "estimated_tokens": self.chunker.estimator.count(prefix + chunk.text),
                **request.metadata,
                "embedding_model": self.settings.embedding_model,
            }
            for chunk in chunks
        ]
        # Older Spring AI writes used the business document id directly in the
        # same collection. Remove that legacy row before writing runtime-owned
        # chunks so one document cannot occupy the ranking twice.
        await asyncio.to_thread(collection.delete, ids=[request.document_id])
        await asyncio.to_thread(
            collection.upsert,
            ids=ids,
            embeddings=embeddings,
            documents=texts,
            metadatas=metadatas,
        )
        try:
            await asyncio.to_thread(
                collection.delete,
                where={
                    "$and": [
                        {"document_id": request.document_id},
                        {"revision": {"$ne": revision}},
                    ]
                },
            )
        except Exception:
            log.warning(
                "New revision indexed but stale chunks could not be cleaned for %s",
                request.document_id,
                exc_info=True,
            )
        await self._bump_cache_version()
        self._sparse = None
        return len(chunks)

    async def delete_document(self, document_id: str) -> None:
        collection = await self._ensure_collection()
        await asyncio.to_thread(collection.delete, where={"document_id": document_id})
        await self._bump_cache_version()
        self._sparse = None

    async def search(
        self,
        query: str,
        category: str | None = None,
        limit: int | None = None,
    ) -> list[RagHit]:
        return (await self.search_with_metrics(query, category, limit)).hits

    async def search_with_metrics(
        self,
        query: str,
        category: str | None = None,
        limit: int | None = None,
        *,
        bypass_cache: bool = False,
    ) -> RagSearchResult:
        """Dense (Chroma) + sparse (BM25) retrieval fused with reciprocal rank fusion."""
        started = time.perf_counter()
        safe_limit = min(10, max(1, limit or self.settings.rag_final_k))
        cache_key = await self._cache_key(query, category, safe_limit)
        if not bypass_cache and isinstance(cached := await self._cache_get(cache_key), dict):
            metrics = RagSearchMetrics.model_validate({**cached.get("metrics", {}), "cacheHit": True,
                                                       "durationMs": self._duration_ms(started)})
            self._record_retrieval(metrics)
            return RagSearchResult(hits=[RagHit.model_validate(h) for h in cached.get("hits", [])],
                                   metrics=metrics)

        collection = await self._ensure_collection()
        count = await asyncio.to_thread(collection.count)
        if count <= 0:
            metrics = RagSearchMetrics(durationMs=self._duration_ms(started))
            self._record_retrieval(metrics)
            return RagSearchResult(metrics=metrics)

        # Dense: nearest chunks by cosine similarity of the plain query.
        embedding = (await self.ollama.embed([query], purpose="rag_query_embedding"))[0]
        dense = await asyncio.to_thread(
            collection.query, query_embeddings=[embedding],
            n_results=min(self.settings.rag_dense_k, count),
            where={"category": category} if category else None,
            include=["documents", "metadatas", "distances"])
        pool: dict[str, dict] = {}
        for rank, (item_id, text, meta, distance) in enumerate(zip(
                self._first(dense.get("ids")), self._first(dense.get("documents")),
                self._first(dense.get("metadatas")), self._first(dense.get("distances"))), start=1):
            pool[item_id] = {"id": item_id, "content": text or "", "meta": meta or {},
                             "dense_rank": rank,
                             "dense_score": max(0.0, min(1.0, 1.0 - float(distance if distance is not None else 1)))}

        # Sparse: BM25 over the cached index, same category filter.
        index = await self._sparse_index(collection, count)
        allowed = None if not category else {i for i, m in enumerate(index.metadatas)
                                             if (m or {}).get("category") == category}
        sparse = [hit for hit in index.bm25.search(query, self.settings.rag_sparse_k * 3)
                  if allowed is None or hit.index in allowed][: self.settings.rag_sparse_k]
        top_sparse = sparse[0].score if sparse else 1.0
        for rank, hit in enumerate(sparse, start=1):
            item_id = index.ids[hit.index]
            entry = pool.setdefault(item_id, {"id": item_id, "content": index.documents[hit.index],
                                              "meta": index.metadatas[hit.index] or {}, "dense_score": 0.0})
            entry.update(sparse_rank=rank, sparse_score=hit.score / max(1e-6, top_sparse))

        # Fuse by rank, not by raw scores: the two score scales are not comparable.
        for entry in pool.values():
            entry["rrf"] = sum(1 / (_RRF_K + entry[key]) for key in ("dense_rank", "sparse_rank") if key in entry)
        ranked = sorted(pool.values(), key=lambda e: e["rrf"], reverse=True)

        hits: list[RagHit] = []
        per_document: dict[str, int] = {}
        for entry in ranked:
            meta = entry["meta"]
            # A weak vector match with no keyword overlap is noise, not evidence.
            if "sparse_rank" not in entry and entry["dense_score"] < self.settings.rag_min_score:
                continue
            document_id = str(meta.get("document_id") or entry["id"])
            if per_document.get(document_id, 0) >= 2:
                continue
            per_document[document_id] = per_document.get(document_id, 0) + 1
            both = "dense_rank" in entry and "sparse_rank" in entry
            hits.append(RagHit(
                id=entry["id"], document_id=document_id, content=entry["content"],
                category=str(meta.get("category") or "default"), source=str(meta.get("source") or "unknown"),
                rank=len(hits) + 1, relevance_score=round(entry["dense_score"], 4),
                confidence_label=self._confidence(entry["dense_score"]),
                match_reason="向量与关键词双路命中" if both else
                "关键词命中" if "sparse_rank" in entry else "向量语义召回",
                dense_score=round(entry["dense_score"], 4),
                sparse_score=round(entry.get("sparse_score", 0.0), 4)))
            if len(hits) >= safe_limit:
                break

        metrics = RagSearchMetrics(
            durationMs=self._duration_ms(started), corpusSize=count,
            denseCandidates=sum("dense_rank" in e for e in pool.values()),
            sparseCandidates=len(sparse), fusedCandidates=len(pool), returned=len(hits),
            evidence=self._evidence(hits))
        if not bypass_cache:
            await self._cache_set(cache_key, {"hits": [h.model_dump() for h in hits],
                                              "metrics": metrics.model_dump(by_alias=True)})
        self._record_retrieval(metrics)
        return RagSearchResult(hits=hits, metrics=metrics)

    async def _sparse_index(self, collection, count: int) -> _SparseIndex:
        """Rebuild BM25 only when the collection changed (count or index version)."""
        signature = (count, await self._index_version())
        if self._sparse is not None and self._sparse.signature == signature:
            return self._sparse
        async with self._sparse_lock:
            if self._sparse is None or self._sparse.signature != signature:
                rows = await asyncio.to_thread(collection.get, limit=self.settings.rag_corpus_limit,
                                               include=["documents", "metadatas"])
                documents = [d or "" for d in rows.get("documents") or []]
                self._sparse = _SparseIndex(signature, list(rows.get("ids") or []), documents,
                                            list(rows.get("metadatas") or []), BM25(documents))
        return self._sparse

    async def _index_version(self) -> str:
        redis = await self._ensure_redis()
        if redis is None:
            return "local"
        try:
            return str(await redis.get("rag:runtime:version") or "0")
        except Exception:
            return "local"

    @staticmethod
    def _evidence(hits: list[RagHit]) -> str:
        """Tell the agent how much to trust this result set."""
        if not hits:
            return "none"
        return "strong" if any(h.confidence_label in {"high", "medium"} for h in hits) else "weak"

    async def _ensure_collection(self) -> Any:
        if self._collection is not None:
            return self._collection
        async with self._connect_lock:
            if self._collection is not None:
                return self._collection
            import chromadb

            parsed = urlparse(self.settings.chroma_host)
            host = parsed.hostname or self.settings.chroma_host.replace("http://", "")
            ssl = parsed.scheme == "https"
            client = await asyncio.to_thread(
                chromadb.HttpClient,
                host=host,
                port=self.settings.chroma_port,
                ssl=ssl,
                tenant=self.settings.chroma_tenant,
                database=self.settings.chroma_database,
            )
            self._collection = await asyncio.to_thread(
                client.get_or_create_collection,
                name=self.settings.chroma_collection,
                metadata={"hnsw:space": "cosine", "schema_version": "2"},
            )
        return self._collection

    async def _ensure_redis(self) -> Any:
        if self._redis is not None:
            return self._redis
        try:
            from redis.asyncio import Redis

            client = Redis.from_url(self.settings.redis_url, decode_responses=True)
            await client.ping()
            self._redis = client
        except Exception:
            log.warning("Redis unavailable; RAG cache disabled", exc_info=True)
            self._redis = None
        return self._redis

    async def _cache_key(self, query: str, category: str | None, limit: int) -> str:
        redis = await self._ensure_redis()
        version = "offline"
        if redis is not None:
            version = await redis.get("rag:runtime:version") or "0"
        digest = hashlib.sha256(
            f"{_RETRIEVAL_SCHEMA_VERSION}|{query.strip()}|{category or 'all'}|{limit}".encode()
        ).hexdigest()
        return f"rag:runtime:v{version}:{digest}"

    async def _cache_get(self, key: str) -> Any | None:
        redis = await self._ensure_redis()
        if redis is None:
            return None
        try:
            value = await redis.get(key)
            return None if value is None else json.loads(value)
        except (RedisError, json.JSONDecodeError, TypeError):
            return None

    async def _cache_set(self, key: str, value: Any) -> None:
        redis = await self._ensure_redis()
        if redis is None:
            return
        try:
            await redis.setex(
                key,
                self.settings.rag_cache_ttl_seconds,
                json.dumps(value, ensure_ascii=False),
            )
        except Exception:
            log.debug("Unable to write RAG cache", exc_info=True)

    async def _bump_cache_version(self) -> None:
        redis = await self._ensure_redis()
        if redis is not None:
            try:
                await redis.incr("rag:runtime:version")
            except Exception:
                log.debug("Unable to invalidate RAG cache", exc_info=True)

    @staticmethod
    def _first(value: Any) -> list[Any]:
        if isinstance(value, list) and value and isinstance(value[0], list):
            return value[0]
        return value or []

    @staticmethod
    def _confidence(score: float) -> str:
        if score >= 0.72:
            return "high"
        if score >= 0.48:
            return "medium"
        return "low"

    @staticmethod
    def _duration_ms(started: float) -> float:
        return round((time.perf_counter() - started) * 1000, 2)

    @staticmethod
    def _record_retrieval(metrics: RagSearchMetrics) -> None:
        telemetry = current_run()
        if telemetry is not None:
            telemetry.record_retrieval(metrics.model_dump(by_alias=True))
