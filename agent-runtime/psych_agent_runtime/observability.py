from __future__ import annotations

import time
import uuid
from contextvars import ContextVar

from fastapi import FastAPI, Request
from prometheus_client import CONTENT_TYPE_LATEST, Counter, Histogram, generate_latest
from starlette.responses import Response

request_id_context: ContextVar[str] = ContextVar("request_id", default="-")

HTTP_REQUESTS = Counter(
    "psych_agent_runtime_http_requests_total",
    "HTTP requests handled by the agent runtime",
    ("method", "route", "status"),
)
HTTP_LATENCY = Histogram(
    "psych_agent_runtime_http_request_duration_seconds",
    "Time spent creating an HTTP response",
    ("method", "route"),
)
AGENT_RUN_DURATION = Histogram(
    "psych_agent_run_duration_seconds",
    "End-to-end LangGraph run duration",
    ("status",),
)
AGENT_TTFT = Histogram(
    "psych_agent_time_to_first_token_seconds",
    "Time from runtime invocation to first response token",
)
AGENT_NODE_DURATION = Histogram(
    "psych_agent_node_duration_seconds",
    "LangGraph node execution duration",
    ("node",),
)
MODEL_TOKENS = Counter(
    "psych_agent_model_tokens_total",
    "Ollama tokens processed by purpose and token type",
    ("model", "purpose", "token_type"),
)
MODEL_CALL_DURATION = Histogram(
    "psych_agent_model_call_duration_seconds",
    "Ollama model call duration",
    ("model", "purpose"),
)
MODEL_COST = Counter(
    "psych_agent_model_cost_usd_total",
    "Accumulated model cost in USD",
    ("cost_type",),
)
RAG_RETRIEVAL_DURATION = Histogram(
    "psych_agent_rag_retrieval_duration_seconds",
    "Hybrid RAG retrieval duration",
    ("cache_hit",),
)
RAG_RETURNED = Histogram(
    "psych_agent_rag_returned_documents",
    "Number of RAG chunks returned per retrieval",
)
AGENT_FALLBACKS = Counter(
    "psych_agent_fallback_total",
    "Number of bounded runtime fallbacks",
)


def install_observability(app: FastAPI) -> None:
    @app.middleware("http")
    async def observe_request(request: Request, call_next):
        request_id = request.headers.get("X-Request-ID") or str(uuid.uuid4())
        context_token = request_id_context.set(request_id)
        route = request.url.path
        started = time.perf_counter()
        status = 500
        try:
            response = await call_next(request)
            status = response.status_code
            response.headers["X-Request-ID"] = request_id
            return response
        finally:
            HTTP_REQUESTS.labels(request.method, route, str(status)).inc()
            HTTP_LATENCY.labels(request.method, route).observe(time.perf_counter() - started)
            request_id_context.reset(context_token)


def metrics_response() -> Response:
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)


def record_agent_run(metrics: dict) -> None:
    status = str(metrics.get("status") or "unknown")
    AGENT_RUN_DURATION.labels(status).observe(
        float(metrics.get("totalDurationMs") or 0) / 1000
    )
    ttft = metrics.get("ttftMs")
    if ttft is not None:
        AGENT_TTFT.observe(float(ttft) / 1000)
    for node, duration_ms in (metrics.get("nodeDurationsMs") or {}).items():
        AGENT_NODE_DURATION.labels(str(node)).observe(float(duration_ms) / 1000)
    for call in metrics.get("modelCalls") or []:
        model = str(call.get("model") or "unknown")
        purpose = str(call.get("purpose") or "unknown")
        MODEL_TOKENS.labels(model, purpose, "prompt").inc(
            int(call.get("promptTokens") or 0)
        )
        MODEL_TOKENS.labels(model, purpose, "completion").inc(
            int(call.get("completionTokens") or 0)
        )
        MODEL_CALL_DURATION.labels(model, purpose).observe(
            float(call.get("durationMs") or 0) / 1000
        )
    MODEL_COST.labels("actual").inc(float(metrics.get("actualCostUsd") or 0))
    MODEL_COST.labels("estimated").inc(float(metrics.get("estimatedCostUsd") or 0))
    retrieval = metrics.get("retrieval") or {}
    if retrieval:
        RAG_RETRIEVAL_DURATION.labels(
            str(bool(retrieval.get("cacheHit"))).lower()
        ).observe(float(retrieval.get("durationMs") or 0) / 1000)
        RAG_RETURNED.observe(int(retrieval.get("returned") or 0))
    fallback_count = int(metrics.get("fallbackCount") or 0)
    if fallback_count:
        AGENT_FALLBACKS.inc(fallback_count)
