from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class ChatMessage(BaseModel):
    role: Literal["user", "assistant", "system", "tool"]
    content: str
    created_at: datetime | None = Field(default=None, alias="createdAt")

    model_config = ConfigDict(populate_by_name=True)


class UserProfile(BaseModel):
    profile_summary: str = Field(default="", alias="profileSummary")
    concerns: str = ""
    preferences: str = ""
    coping_strategies: str = Field(default="", alias="copingStrategies")
    risk_signals: str = Field(default="", alias="riskSignals")

    model_config = ConfigDict(populate_by_name=True)


class AgentContext(BaseModel):
    username: str
    session_id: int = Field(alias="sessionId")
    session_summary: str = Field(default="", alias="sessionSummary")
    medium_term_summary: str = Field(default="", alias="mediumTermSummary")
    long_term_memory: str = Field(default="", alias="longTermMemory")
    profile: UserProfile = Field(default_factory=UserProfile)
    messages: list[ChatMessage] = Field(default_factory=list)

    model_config = ConfigDict(populate_by_name=True)


class AgentRequest(BaseModel):
    username: str = Field(min_length=1, max_length=128)
    session_id: int = Field(alias="sessionId", gt=0)
    message: str = Field(min_length=1, max_length=4000)
    request_id: str = Field(alias="requestId", min_length=1, max_length=128)

    model_config = ConfigDict(populate_by_name=True)


class HelpResource(BaseModel):
    name: str
    type: str
    contact: str
    description: str
    urgent: bool


class RagHit(BaseModel):
    id: str
    document_id: str
    content: str
    category: str
    source: str
    rank: int = 0
    relevance_score: float = 0.0
    confidence_label: str = "low"
    match_reason: str = ""
    dense_score: float = 0.0
    sparse_score: float = 0.0


class RagTrace(BaseModel):
    request_id: str = Field(alias="requestId")
    tool: str = "search_knowledge"
    query: str
    category: str | None = None
    citation_count: int = Field(alias="citationCount")
    status: str = "completed"
    executed_at: datetime = Field(default_factory=datetime.now, alias="executedAt")
    citations: list[dict[str, Any]] = Field(default_factory=list)
    retrieval_metrics: dict[str, Any] = Field(default_factory=dict, alias="retrievalMetrics")

    model_config = ConfigDict(populate_by_name=True)


class RunMetrics(BaseModel):
    request_id: str = Field(alias="requestId")
    model: str
    started_at: datetime = Field(alias="startedAt")
    status: str = "success"
    total_duration_ms: float = Field(alias="totalDurationMs")
    ttft_ms: float | None = Field(default=None, alias="ttftMs")
    prompt_tokens: int = Field(default=0, alias="promptTokens")
    completion_tokens: int = Field(default=0, alias="completionTokens")
    total_tokens: int = Field(default=0, alias="totalTokens")
    model_call_count: int = Field(default=0, alias="modelCallCount")
    model_calls: list[dict[str, Any]] = Field(default_factory=list, alias="modelCalls")
    tokens_per_second: float = Field(default=0.0, alias="tokensPerSecond")
    actual_cost_usd: float = Field(default=0.0, alias="actualCostUsd")
    estimated_cost_usd: float = Field(default=0.0, alias="estimatedCostUsd")
    cost_mode: str = Field(default="local", alias="costMode")
    node_durations_ms: dict[str, float] = Field(default_factory=dict, alias="nodeDurationsMs")
    retrieval: dict[str, Any] = Field(default_factory=dict)
    mcp_duration_ms: float = Field(default=0.0, alias="mcpDurationMs")
    fallback_count: int = Field(default=0, alias="fallbackCount")
    persistence_status: str = Field(default="saved", alias="persistenceStatus")
    pipeline: dict[str, Any] = Field(default_factory=dict)
    capability_calls: list[dict[str, Any]] = Field(default_factory=list, alias="capabilityCalls")

    model_config = ConfigDict(populate_by_name=True)


class AgentResponse(BaseModel):
    reply: str
    used_tool: bool = Field(alias="usedTool")
    tool_name: str | None = Field(default=None, alias="toolName")
    session_id: int = Field(alias="sessionId")
    crisis: bool = False
    help_center_url: str | None = Field(default=None, alias="helpCenterUrl")
    resources: list[HelpResource] = Field(default_factory=list)
    rag_trace: RagTrace | None = Field(default=None, alias="ragTrace")
    metrics: RunMetrics | None = None

    model_config = ConfigDict(populate_by_name=True)


class RagSearchMetrics(BaseModel):
    duration_ms: float = Field(default=0.0, alias="durationMs")
    cache_hit: bool = Field(default=False, alias="cacheHit")
    corpus_size: int = Field(default=0, alias="corpusSize")
    dense_candidates: int = Field(default=0, alias="denseCandidates")
    sparse_candidates: int = Field(default=0, alias="sparseCandidates")
    fused_candidates: int = Field(default=0, alias="fusedCandidates")
    returned: int = 0
    evidence: str = "none"  # strong | weak | none, from the best vector similarity

    model_config = ConfigDict(populate_by_name=True)


class RagSearchResult(BaseModel):
    hits: list[RagHit] = Field(default_factory=list)
    metrics: RagSearchMetrics = Field(default_factory=RagSearchMetrics)


class KnowledgeUpsertRequest(BaseModel):
    document_id: str
    content: str = Field(min_length=1)
    category: str = "default"
    source: str = "unknown"
    chunk_size: int = Field(default=450, ge=120, le=1200)
    overlap: int = Field(default=80, ge=0, le=300)
    metadata: dict[
        Literal["title", "keywords", "section", "language", "license", "corpusVersion",
                "reviewedAt", "sourceReviewedAt", "contentSha256", "sourceSha256",
                "translationProvider", "translationReviewStatus", "publisher", "licenseSource",
                "audience", "contentKind"],
        Annotated[str, Field(max_length=500)],
    ] = Field(default_factory=dict, max_length=16)


class KnowledgeDeleteRequest(BaseModel):
    document_id: str


class KnowledgeSearchRequest(BaseModel):
    query: str = Field(min_length=1)
    category: str | None = None
    limit: int = Field(default=6, ge=1, le=10)
    request_id: str | None = None


class MemoryCompression(BaseModel):
    session_summary: str = ""
    medium_term_summary: str = ""
    long_term_memory: str = ""
    profile_patch: dict[str, str] = Field(default_factory=dict)
    compressed: bool = False


class MemoryEvidence(BaseModel):
    id: str
    content: str
    truncated: bool = False
    observedAt: str
    timestampRole: Literal["observation_time"]
    sessionId: int
    source: str
    role: Literal["user"]


class MemorySearchResult(BaseModel):
    evidence: list[MemoryEvidence]
    status: Literal["empty", "ranked", "recent_only", "unavailable"] = "empty"
    retrievalCoverage: dict[str, Any] = Field(default_factory=dict)
