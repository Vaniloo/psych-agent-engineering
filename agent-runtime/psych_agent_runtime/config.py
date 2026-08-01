from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _env_float(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        return float(raw)
    except ValueError:
        return default


@dataclass(frozen=True)
class Settings:
    service_name: str = os.getenv("SERVICE_NAME", "psych-agent-runtime")
    ollama_base_url: str = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
    chat_model: str = os.getenv("OLLAMA_CHAT_MODEL", "qwen3.5:4b")
    embedding_model: str = os.getenv("OLLAMA_EMBEDDING_MODEL", "nomic-embed-text")
    ollama_timeout_seconds: int = _env_int("OLLAMA_TIMEOUT_SECONDS", 120)
    ollama_think: bool = _env_bool("OLLAMA_THINK", False)

    cost_mode: str = os.getenv("MODEL_COST_MODE", "local")
    input_cost_per_million_tokens: float = _env_float(
        "INPUT_COST_PER_MILLION_TOKENS", 0.0
    )
    output_cost_per_million_tokens: float = _env_float(
        "OUTPUT_COST_PER_MILLION_TOKENS", 0.0
    )

    chroma_host: str = os.getenv("CHROMA_HOST", "http://localhost")
    chroma_port: int = _env_int("CHROMA_PORT", 8000)
    chroma_tenant: str = os.getenv("CHROMA_TENANT", "default_tenant")
    chroma_database: str = os.getenv("CHROMA_DATABASE", "default_database")
    chroma_collection: str = os.getenv("CHROMA_COLLECTION", "psych-agent-knowledge-v2")

    redis_url: str = os.getenv("REDIS_URL", "redis://localhost:6379/0")
    checkpoint_backend: str = os.getenv("CHECKPOINT_BACKEND", "redis")
    rag_cache_ttl_seconds: int = _env_int("RAG_CACHE_TTL_SECONDS", 600)

    backend_base_url: str = os.getenv("BACKEND_BASE_URL", "http://localhost:8080")
    internal_service_token: str = os.getenv(
        "INTERNAL_SERVICE_TOKEN", "change-internal-service-token"
    )
    mcp_server_url: str = os.getenv("MCP_SERVER_URL", "http://localhost:8001/mcp")
    mcp_host: str = os.getenv("MCP_HOST", "0.0.0.0")
    mcp_port: int = _env_int("MCP_PORT", 8001)

    context_window_tokens: int = _env_int("CONTEXT_WINDOW_TOKENS", 8192)
    response_reserve_tokens: int = _env_int("RESPONSE_RESERVE_TOKENS", 1200)
    memory_compression_trigger_tokens: int = _env_int(
        "MEMORY_COMPRESSION_TRIGGER_TOKENS", 4200
    )
    max_history_messages: int = _env_int("MAX_HISTORY_MESSAGES", 40)

    rag_dense_k: int = _env_int("RAG_DENSE_K", 20)
    rag_sparse_k: int = _env_int("RAG_SPARSE_K", 20)
    rag_final_k: int = _env_int("RAG_FINAL_K", 6)
    rag_min_score: float = _env_float("RAG_MIN_SCORE", 0.18)
    rag_llm_rerank_enabled: bool = _env_bool("RAG_LLM_RERANK_ENABLED", False)
    rag_corpus_limit: int = _env_int("RAG_CORPUS_LIMIT", 5000)
    embedding_batch_size: int = _env_int("EMBEDDING_BATCH_SIZE", 32)
    rag_evaluation_dataset: str = os.getenv(
        "RAG_EVALUATION_DATASET", "evaluation/rag_dataset.json"
    )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
