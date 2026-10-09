from __future__ import annotations

from typing import Any

from mcp.server.fastmcp import FastMCP

from .backend_client import BackendClient
from .capabilities import CapabilityExecutor
from .config import get_settings
from .ollama_client import OllamaClient
from .rag import RagEngine

settings = get_settings()
backend = BackendClient(settings)
ollama = OllamaClient(settings)
rag = RagEngine(settings, ollama)
executor = CapabilityExecutor(backend, rag)

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
    return await executor.execute("get_user_context", {"username": username, "session_id": session_id})


@mcp.tool()
async def search_knowledge(
    query: str,
    category: str | None = None,
    limit: int = 6,
) -> dict[str, Any]:
    """Run hybrid dense+sparse retrieval over the psychology knowledge base."""
    return await executor.execute("search_knowledge", {"query": query, "category": category, "limit": limit})


@mcp.tool()
async def get_dashboard(username: str) -> dict[str, Any]:
    """Return the current user's recent reports and scoped aggregate metrics."""
    return await executor.execute("get_dashboard", {"username": username})


@mcp.tool()
async def crisis_support(username: str, message: str) -> dict[str, Any]:
    """Persist a crisis event and return the fixed, deterministic help response."""
    return await executor.execute("crisis_support", {"username": username, "message": message})


@mcp.tool()
async def persist_turn(
    username: str,
    session_id: int,
    user_message: str,
    assistant_reply: str,
    compression: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Persist a completed turn and any bounded compressed-memory update."""
    return await executor.execute("persist_turn", {
        "username": username, "session_id": session_id,
        "user_message": user_message, "assistant_reply": assistant_reply,
        "compression": compression or {},
    })


@mcp.tool()
async def get_support_resources() -> list[dict[str, Any]]:
    """Read vetted help resources without creating a crisis event."""
    return await executor.execute("get_support_resources", {})



def main() -> None:
    mcp.run(transport="streamable-http")


if __name__ == "__main__":
    main()
