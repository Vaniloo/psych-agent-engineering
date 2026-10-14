from __future__ import annotations

from typing import Any

from mcp.server.fastmcp import FastMCP

from .backend_client import BackendClient
from .capabilities import CapabilityExecutor
from .config import get_settings
from .memory import MemoryService
from .ollama_client import OllamaClient
from .rag import RagEngine

settings = get_settings()
backend = BackendClient(settings)
ollama = OllamaClient(settings)
rag = RagEngine(settings, ollama)
memory = MemoryService(settings, backend)
executor = CapabilityExecutor(backend, rag, memory)

mcp = FastMCP(
    "Psych Agent Tools",
    instructions=(
        "受控心理支持工具。身份字段只能由受信任的 Agent Runtime 注入；"
        "高风险工具不能被角色卡或自由生成覆盖。"
    ),
    host=settings.mcp_host,
    port=settings.mcp_port,
    streamable_http_path="/mcp",
    stateless_http=True,
    json_response=True,
)


@mcp.tool()
async def risk_scan(text: str) -> dict[str, Any]:
    """Deterministically scan text for explicit high-risk language."""
    return await executor.execute("risk_scan", {"text": text})


@mcp.tool()
async def get_user_context(username: str, session_id: int) -> dict[str, Any]:
    """Load structured short, medium, and long-term memory for one owned session."""
    return await executor.execute(
        "get_user_context", {"username": username, "session_id": session_id}
    )


@mcp.tool()
async def search_knowledge(
    query: str,
    category: str | None = None,
    limit: int = 6,
) -> dict[str, Any]:
    """Run hybrid dense+sparse retrieval over the psychology knowledge base."""
    return await executor.execute(
        "search_knowledge", {"query": query, "category": category, "limit": limit}
    )


@mcp.tool()
async def get_dashboard(username: str) -> dict[str, Any]:
    """Return the current user's recent reports and scoped aggregate metrics."""
    return await executor.execute("get_dashboard", {"username": username})


@mcp.tool()
async def crisis_support(username: str, message: str) -> dict[str, Any]:
    """Persist a crisis event and return the fixed, deterministic help response."""
    return await executor.execute("crisis_support", {"username": username, "message": message})


@mcp.tool()
async def begin_turn(username: str, session_id: int, user_message: str, request_id: str) -> dict:
    """Record an owned original request before a guarded goal write; no memory job yet."""
    return await executor.execute("begin_turn", {
        "username": username, "session_id": session_id,
        "user_message": user_message, "request_id": request_id,
    })


@mcp.tool()
async def persist_turn(
    username: str,
    session_id: int,
    user_message: str,
    assistant_reply: str,
    request_id: str,
    compression: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Persist a completed turn and any bounded compressed-memory update."""
    return await executor.execute(
        "persist_turn",
        {
            "username": username,
            "session_id": session_id,
            "user_message": user_message,
            "assistant_reply": assistant_reply,
            "compression": compression or {},
            "request_id": request_id,
        },
    )


@mcp.tool()
async def get_support_resources() -> list[dict[str, Any]]:
    """Read vetted help resources without creating a crisis event."""
    return await executor.execute("get_support_resources", {})


@mcp.tool()
async def get_goal_context(username: str, title: str | None = None, status: str | None = None) -> dict[str, Any]:
    """Read owned goals, auto-sync preference and action material."""
    return await executor.execute("get_goal_context", {"username": username, "title": title, "status": status})


@mcp.tool()
async def memory_search(
    username: str,
    query: str,
    limit: int = 6,
    purpose: str = "lookup",
    session_id: int | None = None,
    time_window: str | None = None,
    recent_messages: bool = False,
) -> dict[str, Any]:
    """Retrieve attributed user evidence through the independent memory subsystem."""
    return await executor.execute(
        "memory_search",
        {
            "username": username,
            "query": query,
            "limit": limit,
            "purpose": purpose,
            "session_id": session_id,
            "time_window": time_window,
            "recent_messages": recent_messages,
        },
    )


def main() -> None:
    import uvicorn

    from .service_auth import InternalAuthMiddleware

    app = mcp.streamable_http_app()
    app.add_middleware(InternalAuthMiddleware, token=settings.internal_service_token)
    uvicorn.run(app, host=settings.mcp_host, port=settings.mcp_port)


@mcp.tool()
async def sync_goal_progress(
    username: str,
    session_id: int,
    request_id: str,
    goal_id: int,
    action_id: str,
    version: int,
    outcome: str,
    quote: str,
) -> dict:
    """Update one existing owned action from an already saved user turn."""
    return await executor.execute(
        "sync_goal_progress",
        {
            "username": username,
            "session_id": session_id,
            "request_id": request_id,
            "goal_id": goal_id,
            "action_id": action_id,
            "version": version,
            "outcome": outcome,
            "quote": quote,
        },
    )


@mcp.tool()
async def create_goal(
    username: str, session_id: int, request_id: str, source_goal_title: str | None = None
) -> dict:
    """Create owned ordered steps from an explicit, already persisted user command."""
    return await executor.execute(
        "create_goal",
        {
            "username": username,
            "session_id": session_id,
            "request_id": request_id,
            "source_goal_title": source_goal_title,
        },
    )


if __name__ == "__main__":
    main()
