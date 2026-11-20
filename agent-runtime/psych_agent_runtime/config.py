from __future__ import annotations

import os
from dataclasses import dataclass, field
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
    goal_gate_mode: str = "off"
    goal_gate_users: str = ""
    goal_gate_base_url: str = os.getenv("GOAL_GATE_BASE_URL", "http://127.0.0.1:18102")
    goal_gate_api_key: str = field(default=os.getenv("GOAL_GATE_API_KEY", ""), repr=False)
    goal_gate_timeout_seconds: float = _env_float("GOAL_GATE_TIMEOUT_SECONDS", 8)
    goal_gate_expected_sha: str = os.getenv("GOAL_GATE_EXPECTED_SHA", "")
    memory_context_recall_enabled: bool = (
        os.getenv("MEMORY_CONTEXT_RECALL_ENABLED", "true").lower() == "true"
    )
    ai_provider: str = os.getenv("AI_PROVIDER", "ollama")
    deepseek_api_key: str = field(default=os.getenv("DEEPSEEK_API_KEY", ""), repr=False)
    deepseek_base_url: str = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com")
    deepseek_model: str = os.getenv("DEEPSEEK_MODEL", "deepseek-flash")
    service_name: str = os.getenv("SERVICE_NAME", "psych-agent-runtime")
    ollama_base_url: str = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
    chat_model: str = os.getenv("OLLAMA_CHAT_MODEL", "qwen3.5:4b")
    embedding_model: str = os.getenv("OLLAMA_EMBEDDING_MODEL", "nomic-embed-text")
    ollama_timeout_seconds: int = _env_int("OLLAMA_TIMEOUT_SECONDS", 120)
    ollama_think: bool = _env_bool("OLLAMA_THINK", False)

    cost_mode: str = os.getenv("MODEL_COST_MODE", "local")
    input_cost_per_million_tokens: float = _env_float("INPUT_COST_PER_MILLION_TOKENS", 0.0)
    output_cost_per_million_tokens: float = _env_float("OUTPUT_COST_PER_MILLION_TOKENS", 0.0)

    chroma_host: str = os.getenv("CHROMA_HOST", "http://localhost")
    chroma_port: int = _env_int("CHROMA_PORT", 8000)
    chroma_tenant: str = os.getenv("CHROMA_TENANT", "default_tenant")
    chroma_database: str = os.getenv("CHROMA_DATABASE", "default_database")
    chroma_collection: str = os.getenv("CHROMA_COLLECTION", "psych-agent-knowledge-v2")

    redis_url: str = os.getenv("REDIS_URL", "redis://localhost:6379/0")
    checkpoint_backend: str = os.getenv("CHECKPOINT_BACKEND", "redis")
    rag_cache_ttl_seconds: int = _env_int("RAG_CACHE_TTL_SECONDS", 600)

    backend_base_url: str = os.getenv("BACKEND_BASE_URL", "http://localhost:8080")
    internal_service_token: str = field(
        default=os.getenv("INTERNAL_SERVICE_TOKEN", "change-internal-service-token"), repr=False
    )
    mcp_server_url: str = os.getenv("MCP_SERVER_URL", "http://localhost:8001/mcp")
    mcp_host: str = os.getenv("MCP_HOST", "0.0.0.0")
    mcp_port: int = _env_int("MCP_PORT", 8001)
    mcp_read_tools_config: str = os.getenv("MCP_READ_TOOLS_CONFIG", "")
    dynamic_read_stop_review_enabled: bool = _env_bool("DYNAMIC_READ_STOP_REVIEW_ENABLED", True)

    router_provider: str = "rules"
    router_base_url: str = os.getenv("ROUTER_BASE_URL", "http://localhost:8091")
    router_api_key: str = field(default=os.getenv("ROUTER_API_KEY", ""), repr=False)
    router_timeout_seconds: float = _env_float("ROUTER_TIMEOUT_SECONDS", 3.0)
    router_min_probability: float = _env_float("ROUTER_MIN_PROBABILITY", 0.85)

    executor_provider: str = os.getenv("EXECUTOR_PROVIDER", "rules")
    executor_base_url: str = os.getenv("EXECUTOR_BASE_URL", "http://localhost:8093")
    executor_api_key: str = field(default=os.getenv("EXECUTOR_API_KEY", ""), repr=False)
    executor_timeout_seconds: float = _env_float("EXECUTOR_TIMEOUT_SECONDS", 2.0)
    executor_min_probability: float = _env_float("EXECUTOR_MIN_PROBABILITY", 0.85)
    executor_adapter_sha256: str = os.getenv(
        "EXECUTOR_ADAPTER_SHA256", "5c526403a928b637774d7e1304a49fa3979224dad3ce5e8437b37d1bdc4f04a7"
    )
    agent_loop_max_tool_calls: int = _env_int("AGENT_LOOP_MAX_TOOL_CALLS", 8)
    agent_loop_timeout_seconds: float = _env_float("AGENT_LOOP_TIMEOUT_SECONDS", 60.0)
    agent_loop_refinement_enabled: bool = (
        os.getenv("AGENT_LOOP_REFINEMENT_ENABLED", "true").lower() == "true"
    )
    # Goal operations use the existing deterministic intent and persistence checks.
    executor_goal_provider: str = "rules"
    executor_goal_base_url: str = os.getenv("EXECUTOR_GOAL_BASE_URL", "")
    executor_goal_api_key: str = field(default=os.getenv("EXECUTOR_GOAL_API_KEY", ""), repr=False)
    executor_goal_timeout_seconds: float = _env_float("EXECUTOR_GOAL_TIMEOUT_SECONDS", 2.0)
    executor_goal_min_probability: float = _env_float("EXECUTOR_GOAL_MIN_PROBABILITY", 0.85)
    memory_provider: str = "rules"
    memory_type_recall_enabled: bool = os.getenv("MEMORY_TYPE_RECALL_ENABLED", "false").lower() == "true"
    memory_contract_version: str = os.getenv("MEMORY_CONTRACT_VERSION", "psych-memory-v2")
    memory_base_url: str = os.getenv("MEMORY_BASE_URL", "http://localhost:8092")
    memory_api_key: str = field(default=os.getenv("MEMORY_API_KEY", ""), repr=False)
    memory_timeout_seconds: float = _env_float("MEMORY_TIMEOUT_SECONDS", 2.0)
    memory_min_probability: float = _env_float("MEMORY_MIN_PROBABILITY", 0.85)
    memory_store_path: str = os.getenv("MEMORY_STORE_PATH", "data/memory.sqlite3")
    memory_lifecycle_shadow_url: str = ""
    memory_lifecycle_shadow_key: str = field(
        default=os.getenv("MEMORY_LIFECYCLE_SHADOW_KEY", ""), repr=False
    )
    memory_lifecycle_shadow_users: str = ""
    memory_lifecycle_sandbox_users: str = ""
    memory_lifecycle_read_users: str = ""
    memory_lifecycle_active_users: str = ""
    memory_lifecycle_expected_sha: str = os.getenv("MEMORY_LIFECYCLE_EXPECTED_SHA", "")
    memory_source_review_enabled: bool = os.getenv("MEMORY_SOURCE_REVIEW_ENABLED", "false").lower() == "true"
    memory_lifecycle_store_path: str = os.getenv(
        "MEMORY_LIFECYCLE_STORE_PATH", "data/memory-lifecycle.sqlite3"
    )
    memory_rerank_base_url: str = os.getenv("MEMORY_RERANK_BASE_URL", "")
    memory_rerank_api_key: str = field(default=os.getenv("MEMORY_RERANK_API_KEY", ""), repr=False)
    memory_rerank_timeout_seconds: float = _env_float("MEMORY_RERANK_TIMEOUT_SECONDS", 2.0)
    memory_recall_recent_limit: int = _env_int("MEMORY_RECALL_RECENT_LIMIT", 24)
    memory_query_timezone: str = os.getenv("MEMORY_QUERY_TIMEZONE", "Asia/Shanghai")
    memory_legacy_timezone: str = os.getenv("MEMORY_LEGACY_TIMEZONE", "UTC")
    tool_transport: str = os.getenv("TOOL_TRANSPORT", "local")

    context_window_tokens: int = _env_int("CONTEXT_WINDOW_TOKENS", 8192)
    response_reserve_tokens: int = _env_int("RESPONSE_RESERVE_TOKENS", 1200)
    max_history_messages: int = _env_int("MAX_HISTORY_MESSAGES", 40)

    rag_dense_k: int = _env_int("RAG_DENSE_K", 20)
    rag_sparse_k: int = _env_int("RAG_SPARSE_K", 20)
    rag_final_k: int = _env_int("RAG_FINAL_K", 6)
    rag_min_score: float = _env_float("RAG_MIN_SCORE", 0.18)
    rag_llm_rerank_enabled: bool = _env_bool("RAG_LLM_RERANK_ENABLED", False)
    rag_rerank_base_url: str = os.getenv("RAG_RERANK_BASE_URL", "")
    rag_rerank_api_key: str = field(default=os.getenv("RAG_RERANK_API_KEY", ""), repr=False)
    rag_rerank_revision: str = os.getenv("RAG_RERANK_REVISION", "")
    rag_rerank_timeout_seconds: float = _env_float("RAG_RERANK_TIMEOUT_SECONDS", 3.0)
    rag_corpus_limit: int = _env_int("RAG_CORPUS_LIMIT", 5000)
    embedding_batch_size: int = _env_int("EMBEDDING_BATCH_SIZE", 32)
    rag_evaluation_dataset: str = os.getenv("RAG_EVALUATION_DATASET", "evaluation/rag_dataset.json")
    agentic_rag_enabled: bool = _env_bool("AGENTIC_RAG_ENABLED", False)
    agentic_rag_model_timeout_seconds: float = _env_float("AGENTIC_RAG_MODEL_TIMEOUT_SECONDS", 5.0)
    agentic_rag_timeout_seconds: float = _env_float("AGENTIC_RAG_TIMEOUT_SECONDS", 15.0)

    web_search_enabled: bool = _env_bool("WEB_SEARCH_ENABLED", False)
    web_search_users: str = os.getenv("WEB_SEARCH_USERS", "")
    web_search_proactive_enabled: bool = _env_bool("WEB_SEARCH_PROACTIVE_ENABLED", False)
    web_role_voice_enabled: bool = _env_bool("WEB_ROLE_VOICE_ENABLED", False)
    web_search_provider: str = os.getenv("WEB_SEARCH_PROVIDER", "searxng")
    web_search_base_url: str = os.getenv("WEB_SEARCH_BASE_URL", "http://127.0.0.1:18095")
    web_search_engines: str = os.getenv("WEB_SEARCH_ENGINES", "360search,yandex")
    web_search_fallback_engines: str = os.getenv("WEB_SEARCH_FALLBACK_ENGINES", "naver,mwmbl")
    web_context_enabled: bool = _env_bool("WEB_CONTEXT_ENABLED", False)
    web_filter_url: str = ""
    web_filter_service_key: str = os.getenv("WEB_FILTER_SERVICE_KEY", "")
    web_filter_timeout_seconds: float = _env_float("WEB_FILTER_TIMEOUT_SECONDS", 2.5)
    web_page_budget_seconds: float = _env_float("WEB_PAGE_BUDGET_SECONDS", 4.0)
    web_provider_timeout_seconds: float = _env_float("WEB_PROVIDER_TIMEOUT_SECONDS", 6.0)
    web_search_api_key: str = os.getenv("WEB_SEARCH_API_KEY", "")
    web_search_cache_dir: str = os.getenv("WEB_SEARCH_CACHE_DIR", "data/web-search-cache")

    @property
    def generation_model(self):
        return self.deepseek_model if self.ai_provider == "deepseek" else self.chat_model


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    provider = os.getenv("EXECUTOR_PROVIDER", "public_qwen")
    if provider not in {"public_qwen", "rules"}:
        raise ValueError("Set EXECUTOR_PROVIDER to public_qwen or rules")
    return Settings(executor_provider=provider)
