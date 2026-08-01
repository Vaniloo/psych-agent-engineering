from __future__ import annotations

from typing import Any

from mcp.server.fastmcp import FastMCP

from .backend_client import BackendClient
from .config import get_settings
from .ollama_client import OllamaClient
from .rag import RagEngine
from .schemas import MemoryCompression

settings = get_settings()
backend = BackendClient(settings)
ollama = OllamaClient(settings)
rag = RagEngine(settings, ollama)

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
    return await backend.risk_scan(text)


@mcp.tool()
async def get_user_context(username: str, session_id: int) -> dict[str, Any]:
    """Load structured short, medium, and long-term memory for one owned session."""
    context = await backend.context(username, session_id)
    return context.model_dump(by_alias=True, mode="json")


@mcp.tool()
async def search_knowledge(
    query: str,
    category: str | None = None,
    limit: int = 6,
) -> dict[str, Any]:
    """Run hybrid dense+sparse retrieval over the psychology knowledge base."""
    result = await rag.search_with_metrics(query, category, min(10, max(1, limit)))
    return result.model_dump(by_alias=True)


@mcp.tool()
async def get_dashboard(username: str) -> dict[str, Any]:
    """Return the current user's recent reports and scoped aggregate metrics."""
    return await backend.dashboard(username)


@mcp.tool()
async def crisis_support(username: str, message: str) -> dict[str, Any]:
    """Persist a crisis event and return the fixed, deterministic help response."""
    return await backend.crisis(username, message)


@mcp.tool()
async def persist_turn(
    username: str,
    session_id: int,
    user_message: str,
    assistant_reply: str,
    compression: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Persist a completed turn and any bounded compressed-memory update."""
    return await backend.persist_turn(
        username=username,
        session_id=session_id,
        user_message=user_message,
        assistant_reply=assistant_reply,
        compression=MemoryCompression.model_validate(compression or {}),
    )


def main() -> None:
    mcp.run(transport="streamable-http")


if __name__ == "__main__":
    main()
