from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

from redis.exceptions import RedisError

from .bm25 import BM25
from .chunking import TokenAwareChunker
from .config import Settings
from .knowledge_sources import licensed_source
from .ollama_client import OllamaClient
from .schemas import (
    KnowledgeUpsertRequest,
    RagHit,
    RagSearchMetrics,
    RagSearchResult,
)
from .telemetry import current_run

log = logging.getLogger(__name__)

_RETRIEVAL_SCHEMA_VERSION = "hybrid-v7"
_QUERY_EXPANSIONS = {
    "睡眠": "失眠 睡不着 入睡 起床 睡前",
    "失眠": "睡眠 睡不着 入睡 起床 睡前",
    "焦虑": "紧张 心慌 担心 呼吸",
    "拖延": "不开始 启动 第一步 任务拆分",
    "压力": "负荷 紧绷 任务 优先级",
    "低落": "情绪低落 没兴趣 行为激活",
    "社交": "人际 评价 暴露练习",
    "完美": "完美主义 交付 足够好",
}
_CATEGORY_HINTS = {
    "sleep": ("睡眠", "失眠", "睡不着", "入睡", "早醒", "熬夜"),
    "anxiety": ("焦虑", "心慌", "担心", "紧张"),
    "exam_anxiety": ("考试", "考前", "考场"),
    "procrastination": ("拖延", "拖着", "不开始"),
    "stress": ("压力", "任务太多", "撑不住"),
    "mood": ("低落", "没兴趣", "提不起兴趣"),
    "panic": ("惊恐", "濒死感"),
    "social_anxiety": ("社交焦虑", "被评价"),
    "perfectionism": ("完美主义", "不敢交付"),
}


@dataclass
class _Candidate:
    id: str
    document_id: str
    content: str
    category: str
    source: str
    dense_score: float = 0.0
    sparse_score: float = 0.0
    dense_rank: int | None = None
    sparse_rank: int | None = None


class RagEngine:
    """Hybrid retriever with Chroma as the durable vector database."""

    def __init__(self, settings: Settings, ollama: OllamaClient) -> None:
        self.settings = settings
        self.ollama = ollama
        self.chunker = TokenAwareChunker()
        self._collection: Any = None
        self._redis: Any = None
        self._connect_lock = asyncio.Lock()

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
        return len(chunks)

    async def delete_document(self, document_id: str) -> None:
        collection = await self._ensure_collection()
        await asyncio.to_thread(collection.delete, where={"document_id": document_id})
        await self._bump_cache_version()

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
        started = time.perf_counter()
        safe_limit = min(10, max(1, limit or self.settings.rag_final_k))
        cache_key = await self._cache_key(query, category, safe_limit)
        cached = None if bypass_cache else await self._cache_get(cache_key)
        if cached is not None:
            if isinstance(cached, dict):
                cached_hits = cached.get("hits") or []
                cached_metrics = dict(cached.get("metrics") or {})
            else:
                cached_hits = cached
                cached_metrics = {}
            hits = [RagHit.model_validate(item) for item in cached_hits]
            cached_metrics.update(
                {
                    "durationMs": self._duration_ms(started),
                    "cacheHit": True,
                    "returned": len(hits),
                }
            )
            metrics = RagSearchMetrics.model_validate(cached_metrics)
            self._record_retrieval(metrics)
            return RagSearchResult(hits=hits, metrics=metrics)

        collection = await self._ensure_collection()
        count = await asyncio.to_thread(collection.count)
        if count <= 0:
            metrics = RagSearchMetrics(
                durationMs=self._duration_ms(started),
                corpusSize=0,
            )
            self._record_retrieval(metrics)
            return RagSearchResult(metrics=metrics)

        where = {"category": category} if category else None
        corpus_result = await asyncio.to_thread(
            collection.get, where=where, limit=self.settings.rag_corpus_limit,
            include=["documents", "metadatas"],
        )
        scoped = self._licensed_topic_corpus(query, corpus_result)
        source_policy = "licensed_topic" if scoped is not None else "mixed"
        if scoped is not None:
            corpus_result = scoped
            document_ids = sorted({m["document_id"] for m in scoped["metadatas"]})
            where = {"document_id": {"$in": document_ids}}
        retrieval_query = query if scoped is not None else self._expand_query(query)
        query_embedding = (
            await self.ollama.embed([retrieval_query], purpose="rag_query_embedding")
        )[0]
        dense_result = await asyncio.to_thread(
            collection.query,
            query_embeddings=[query_embedding],
            n_results=min(self.settings.rag_dense_k, len(corpus_result.get("ids", [])) or count),
            where=where,
            include=["documents", "metadatas", "distances"],
        )

        candidates = self._merge_candidates(
            retrieval_query,
            dense_result,
            corpus_result,
        )
        dense_candidates = sum(
            1 for candidate in candidates.values() if candidate.dense_rank is not None
        )
        sparse_candidates = sum(
            1 for candidate in candidates.values() if candidate.sparse_rank is not None
        )
        ranked = self._rank(candidates, category, self._infer_categories(query))
        hits: list[RagHit] = []
        chunks_per_document: dict[str, int] = {}
        for candidate, score, reason in ranked:
            if score < self.settings.rag_min_score:
                continue
            previous_chunks = chunks_per_document.get(candidate.document_id, 0)
            if previous_chunks >= 2:
                continue
            hits.append(
                RagHit(
                    id=candidate.id,
                    document_id=candidate.document_id,
                    content=candidate.content,
                    category=candidate.category,
                    source=candidate.source,
                    rank=len(hits) + 1,
                    relevance_score=round(score, 4),
                    confidence_label=self._confidence(score),
                    match_reason=reason,
                    dense_score=round(candidate.dense_score, 4),
                    sparse_score=round(candidate.sparse_score, 4),
                )
            )
            chunks_per_document[candidate.document_id] = previous_chunks + 1
            if len(hits) >= safe_limit:
                break

        metrics = RagSearchMetrics(
            durationMs=self._duration_ms(started),
            cacheHit=False,
            corpusSize=count,
            sourcePolicy=source_policy,
            scopedCorpusSize=len(corpus_result.get("ids", [])),
            denseCandidates=dense_candidates,
            sparseCandidates=sparse_candidates,
            fusedCandidates=len(candidates),
            returned=len(hits),
        )
        if not bypass_cache:
            await self._cache_set(
                cache_key,
                {
                    "hits": [hit.model_dump() for hit in hits],
                    "metrics": metrics.model_dump(by_alias=True),
                },
            )
        self._record_retrieval(metrics)
        return RagSearchResult(hits=hits, metrics=metrics)

    @staticmethod
    def _licensed_topic_corpus(query, corpus):
        """Prefer licensed topic evidence for general education, not personal coaching.

        Topic labels come from the imported corpus. A longest shared label
        disambiguates social anxiety from anxiety without a new model call.
        Uncovered questions keep the full existing collection.
        """
        if re.search(r"我|我的|陪我|结合我的", query.replace("自我", "自身")) or not re.search(
            r"什么|哪些|定义|区别|包括|表现|症状|常见反应|关系|如何理解|怎样选择|"
            r"对.*帮助|如何帮助|儿童.*(?:陪伴|支持)|青少年.*(?:陪伴|支持)|\bwhat\b|\bexplain\b",
            query, re.IGNORECASE,
        ):
            return None
        scored = []
        text = query.lower()
        for i, metadata in enumerate(corpus.get("metadatas") or []):
            m = metadata or {}
            if not licensed_source(m) or not m.get("document_id"):
                continue
            strength = 0
            for word in m.get("keywords", "").lower().split():
                if word.isascii():
                    if len(word) >= 3 and word in text: strength = max(strength, len(word))
                else:
                    for size in range(len(word), 1, -1):
                        if any(word[j:j + size] in text for j in range(len(word) - size + 1)):
                            strength = max(strength, size); break
            if strength: scored.append((i, strength))
        if not scored: return None
        best = max(s for _, s in scored)
        indexes = [i for i, s in scored if s == best]
        # A legacy reviewed CBT starter remains eligible alongside NIMH's CBT
        # descriptions, even though it predates structured corpus metadata.
        if re.search(r"CBT|认知行为", query, re.IGNORECASE):
            indexes.extend(i for i, m in enumerate(corpus.get("metadatas") or [])
                           if (m or {}).get("source") == "https://www.nhs.uk/tests-and-treatments/cognitive-behavioural-therapy-cbt/"
                           and (m or {}).get("document_id"))
        return {k: [corpus[k][i] for i in indexes] for k in ("ids", "documents", "metadatas")}

    def _merge_candidates(
        self,
        query: str,
        dense_result: dict[str, Any],
        corpus_result: dict[str, Any],
    ) -> dict[str, _Candidate]:
        candidates: dict[str, _Candidate] = {}
        dense_ids = self._first(dense_result.get("ids"))
        dense_docs = self._first(dense_result.get("documents"))
        dense_metas = self._first(dense_result.get("metadatas"))
        dense_distances = self._first(dense_result.get("distances"))

        for rank, (item_id, content, metadata, distance) in enumerate(
            zip(dense_ids, dense_docs, dense_metas, dense_distances), start=1
        ):
            metadata = metadata or {}
            similarity = max(0.0, min(1.0, 1.0 - float(1.0 if distance is None else distance)))
            candidates[item_id] = _Candidate(
                id=item_id,
                document_id=str(metadata.get("document_id") or metadata.get("id") or item_id),
                content=content or "",
                category=str(metadata.get("category") or "default"),
                source=str(metadata.get("source") or "unknown"),
                dense_score=similarity,
                dense_rank=rank,
            )

        corpus_ids = corpus_result.get("ids") or []
        corpus_docs = corpus_result.get("documents") or []
        corpus_metas = corpus_result.get("metadatas") or []
        bm25 = BM25([doc or "" for doc in corpus_docs])
        sparse_results = bm25.search(query, self.settings.rag_sparse_k)
        max_sparse = sparse_results[0].score if sparse_results else 1.0
        for rank, scored in enumerate(sparse_results, start=1):
            item_id = corpus_ids[scored.index]
            metadata = corpus_metas[scored.index] or {}
            candidate = candidates.get(item_id)
            if candidate is None:
                candidate = _Candidate(
                    id=item_id,
                    document_id=str(metadata.get("document_id") or metadata.get("id") or item_id),
                    content=corpus_docs[scored.index] or "",
                    category=str(metadata.get("category") or "default"),
                    source=str(metadata.get("source") or "unknown"),
                )
                candidates[item_id] = candidate
            candidate.sparse_score = scored.score / max(0.0001, max_sparse)
            candidate.sparse_rank = rank
        return candidates

    def _rank(
        self,
        candidates: dict[str, _Candidate],
        category: str | None,
        inferred_categories: set[str],
    ) -> list[tuple[_Candidate, float, str]]:
        ranked: list[tuple[_Candidate, float, str]] = []
        for candidate in candidates.values():
            dense_rrf = 0.0 if candidate.dense_rank is None else 1 / (60 + candidate.dense_rank)
            sparse_rrf = 0.0 if candidate.sparse_rank is None else 1 / (60 + candidate.sparse_rank)
            rrf = min(1.0, (dense_rrf * 0.65 + sparse_rrf * 0.35) * 61)
            category_boost = 0.05 if category and candidate.category == category else 0.0
            intent_boost = 0.08 if candidate.category in inferred_categories else 0.0
            score = min(
                1.0,
                candidate.dense_score * 0.52
                + candidate.sparse_score * 0.33
                + rrf * 0.15
                + category_boost
                + intent_boost,
            )
            if candidate.dense_rank and candidate.sparse_rank:
                reason = "向量语义与关键词双路命中"
            elif candidate.sparse_rank:
                reason = "中文关键词/BM25 命中"
            else:
                reason = "向量语义召回"
            ranked.append((candidate, score, reason))
        return sorted(ranked, key=lambda item: item[1], reverse=True)

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

    @staticmethod
    def _expand_query(query: str) -> str:
        expansions = [
            expansion for trigger, expansion in _QUERY_EXPANSIONS.items() if trigger in query
        ]
        return " ".join([query, *expansions])

    @staticmethod
    def _infer_categories(query: str) -> set[str]:
        return {
            category
            for category, hints in _CATEGORY_HINTS.items()
            if any(hint in query for hint in hints)
        }

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
