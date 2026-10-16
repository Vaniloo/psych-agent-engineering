from __future__ import annotations

import asyncio
import json
import logging
from contextlib import asynccontextmanager
from typing import Any

import httpx
from fastapi import FastAPI, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from .capabilities import CAPABILITIES
from .config import get_settings
from .decision_contract import profile_contract
from .evaluation import RagEvaluator
from .graph import AgentGraphRuntime
from .observability import (
    install_observability,
    metrics_response,
    request_id_context,
)
from .schemas import (
    AgentRequest,
    KnowledgeDeleteRequest,
    KnowledgeSearchRequest,
    KnowledgeUpsertRequest,
)
from .service_auth import InternalAuthMiddleware

_record_factory = logging.getLogRecordFactory()


def _request_aware_record_factory(*args: Any, **kwargs: Any) -> logging.LogRecord:
    record = _record_factory(*args, **kwargs)
    record.request_id = getattr(record, "request_id", request_id_context.get())
    return record


logging.setLogRecordFactory(_request_aware_record_factory)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s request=%(request_id)s %(message)s",
)
log = logging.getLogger(__name__)

settings = get_settings()
runtime = AgentGraphRuntime(settings)
rag_evaluator = RagEvaluator(settings, runtime.rag)


@asynccontextmanager
async def lifespan(_: FastAPI):
    await runtime.start()
    try:
        yield
    finally:
        await runtime.close()


app = FastAPI(
    title="Psych Agent LangGraph Runtime",
    version="0.1.0",
    lifespan=lifespan,
)
app.add_middleware(InternalAuthMiddleware, token=settings.internal_service_token)
install_observability(app)


@app.get("/health")
async def health() -> dict[str, Any]:
    dependencies = await runtime.rag.health()
    status = "UP" if all(dependencies.values()) else "DEGRADED"
    return {
        "status": status,
        "service": settings.service_name,
        "graph": runtime.graph is not None,
        "dependencies": dependencies,
    }


@app.get("/v1/runtime/info")
async def runtime_info():
    async def profile(name, prefix=None, task_filter=None):
        prefix = prefix or name
        provider = getattr(settings, f"{prefix}_provider")
        contract, questions = profile_contract(
            name, settings.memory_contract_version if name == "memory" else None
        )
        if task_filter is not None:
            questions = {k: v for k, v in questions.items() if k in task_filter}
        result = {
            "profile": name,
            "provider": provider,
            "contract": contract,
            "tasks": list(questions),
            "status": "rules" if provider == "rules" else "unavailable",
        }
        if provider in {"laya", "qwen"}:
            try:
                async with httpx.AsyncClient(timeout=2, trust_env=False) as client:
                    response = await client.get(
                        getattr(settings, f"{prefix}_base_url").rstrip("/") + "/health"
                    )
                    response.raise_for_status()
                    health = response.json()
                    if not isinstance(health, dict) or not isinstance(
                        health.get("tasks", {}), dict
                    ):
                        raise TypeError("invalid worker health")
                    compatible = (
                        health.get("profile") == name and health.get("contractVersion") == contract
                    )
                    result["status"] = (
                        "ready"
                        if compatible and health.get("ready") is True
                        else "not_ready"
                        if compatible
                        else "incompatible"
                    )
                    result["enabledTasks"] = [
                        t
                        for t, enabled in health.get("tasks", {}).items()
                        if enabled is True and t in questions
                    ]
                    result["model"] = health.get("model")
                    result["experimental"] = health.get("experimental", False)
                    if isinstance(health.get("servingFeatures"), dict):
                        result["servingFeatures"] = health["servingFeatures"]
                    if isinstance(health.get("artifact"), dict):
                        result["artifact"] = health["artifact"]
            except (httpx.HTTPError, ValueError, TypeError, KeyError):
                result["status"] = "unavailable"
        return result

    profiles = await asyncio.gather(*(profile(n) for n in ("router", "executor", "memory")))
    if settings.executor_goal_provider:
        executor = profiles[1]
        executor["tasks"] = ["next_action"]
        executor["enabledTasks"] = [
            t for t in executor.get("enabledTasks", []) if t == "next_action"
        ]
        executor["goalDecisions"] = await profile(
            "executor", "executor_goal", {"goal_creation", "goal_action", "goal_outcome"}
        )
        executor["mode"] = "task_split"

    return {
        "graphReady": runtime.graph is not None,
        "generation": {"provider": settings.ai_provider, "model": settings.generation_model},
        "knowledge": await runtime.rag.knowledge_info(),
        "profiles": profiles,
        "capabilities": [
            {"name": n, "scope": c.scope, "transport": settings.tool_transport}
            for n, c in CAPABILITIES.items()
        ],
        "features": {
            "agentLoop": "bounded-v1",
            "agentLoopMaxReadCalls": 4,
            "agentLoopRecoveryLimit": 1 if settings.agent_loop_refinement_enabled else 0,
            "goalResultBeforeReply": True,
            "goalCreation": True,
            "goalConversationFocus": True,
            "completedGoalRecall": True,
            "contextualGoalIntent": True,
            "supportKnowledgeGrounding": True,
            "goalProgressSync": True,
            "goalReflection": f"backend-{settings.ai_provider}",
            "memoryDelivery": "outbox",
            "memoryLifecycle": runtime.memory.lifecycle_info(),
            "memoryReranking": bool(settings.memory_rerank_base_url),
            "crossSessionContext": settings.memory_context_recall_enabled,
            "personalStateRecall": True,
            "memoryRecentMessageBridge": True,
            "memoryQueryTimezone": settings.memory_query_timezone,
            "responseStyle": "warm_expanded_v1",
            "explicitReadGuarantee": True,
            "boundedToolContext": True,
            "reportAvailability": True,
            "memoryQueryFacets": settings.memory_provider == "qwen",
            "memoryRecentRecallLimit": max(0, min(settings.memory_recall_recent_limit, 24))
            if settings.memory_provider == "qwen" and settings.memory_rerank_base_url
            else 0,
        },
    }


@app.get("/metrics", include_in_schema=False)
async def metrics():
    return metrics_response()


@app.post("/v1/agent/chat")
async def chat(request: AgentRequest):
    try:
        response = await runtime.invoke(request)
        return response.model_dump(by_alias=True, mode="json")
    except Exception as exc:
        log.exception("Agent invocation failed")
        raise HTTPException(status_code=503, detail="agent runtime unavailable") from exc


class MemoryUpdateRequest(BaseModel):
    username: str = Field(min_length=1, max_length=128)
    request_id: str = Field(min_length=1, max_length=128)


@app.post("/v1/memory/status")
async def memory_update_status(request: MemoryUpdateRequest):
    return await asyncio.to_thread(runtime.memory.store.delivery_status,
                                   request.username, request.request_id)


@app.post("/v1/agent/stream")
async def stream_chat(request: AgentRequest):
    async def event_stream():
        context_token = request_id_context.set(request.request_id)
        final_state: dict[str, Any] | None = None
        try:
            yield _sse(
                "meta",
                {
                    "requestId": request.request_id,
                    "sessionId": request.session_id,
                    "runtime": "langgraph",
                },
            )
            async for part in runtime.stream(request):
                part_type = part.get("type")
                if part_type == "custom":
                    payload = part.get("data") or {}
                    yield _sse(str(payload.get("event", "message")), payload.get("data") or {})
                elif part_type == "updates":
                    yield _sse(
                        "graph",
                        {"completedNodes": list((part.get("data") or {}).keys())},
                    )
                elif part_type == "values":
                    data = part.get("data")
                    if isinstance(data, dict):
                        final_state = data
            if final_state is None:
                raise RuntimeError("LangGraph stream completed without final state")
            response = runtime.to_response(final_state)
            yield _sse("done", response.model_dump(by_alias=True, mode="json"))
        except Exception as exc:
            log.exception("Agent stream failed")
            yield _sse(
                "error",
                {
                    "requestId": request.request_id,
                    "message": "Agent 执行失败，请稍后重试",
                    "type": type(exc).__name__,
                },
            )
        finally:
            request_id_context.reset(context_token)

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@app.post("/v1/knowledge/upsert")
async def upsert_knowledge(request: KnowledgeUpsertRequest) -> dict[str, Any]:
    try:
        chunk_count = await runtime.rag.upsert_document(request)
        return {
            "documentId": request.document_id,
            "status": "indexed",
            "chunkCount": chunk_count,
        }
    except Exception as exc:
        log.exception("Knowledge indexing failed")
        raise HTTPException(status_code=503, detail="knowledge indexing failed") from exc


@app.post("/v1/knowledge/delete")
async def delete_knowledge(request: KnowledgeDeleteRequest) -> dict[str, Any]:
    await runtime.rag.delete_document(request.document_id)
    return {"documentId": request.document_id, "status": "deleted"}


@app.post("/v1/knowledge/search")
async def search_knowledge(request: KnowledgeSearchRequest) -> dict[str, Any]:
    result = await runtime.rag.search_with_metrics(request.query, request.category, request.limit)
    return {
        "query": request.query,
        "category": request.category,
        "results": [item.model_dump() for item in result.hits],
        "metrics": result.metrics.model_dump(by_alias=True),
    }


@app.post("/v1/evaluation/rag")
async def evaluate_rag() -> dict[str, Any]:
    try:
        return await rag_evaluator.evaluate()
    except Exception as exc:
        log.exception("RAG evaluation failed")
        raise HTTPException(status_code=503, detail="RAG evaluation failed") from exc


def _sse(event: str, data: Any) -> str:
    serialized = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    return f"event: {event}\ndata: {serialized}\n\n"
