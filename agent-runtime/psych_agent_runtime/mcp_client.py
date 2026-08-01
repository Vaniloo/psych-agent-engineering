from __future__ import annotations

import json
import logging
import time
from typing import Any

from .backend_client import BackendClient
from .config import Settings
from .rag import RagEngine
from .schemas import MemoryCompression
from .telemetry import current_run

log = logging.getLogger(__name__)


class McpToolClient:
    """MCP client with explicit local fallbacks for critical safety paths."""

    def __init__(
        self,
        settings: Settings,
        backend: BackendClient,
        rag: RagEngine,
    ) -> None:
        self.settings = settings
        self.backend = backend
        self.rag = rag

    async def call(self, name: str, arguments: dict[str, Any]) -> Any:
        started = time.perf_counter()
        try:
            from mcp import ClientSession
            from mcp.client.streamable_http import streamable_http_client

            async with streamable_http_client(self.settings.mcp_server_url) as (
                read_stream,
                write_stream,
                _,
            ), ClientSession(read_stream, write_stream) as session:
                await session.initialize()
                result = await session.call_tool(name, arguments=arguments)
                if getattr(result, "isError", False) or getattr(result, "is_error", False):
                    raise RuntimeError(f"MCP tool failed: {name}")
                structured = getattr(result, "structuredContent", None) or getattr(
                    result, "structured_content", None
                )
                if structured is not None:
                    self._record_call(started, fallback=False)
                    return structured
                for item in getattr(result, "content", []):
                    text = getattr(item, "text", None)
                    if text:
                        try:
                            parsed = json.loads(text)
                            self._record_call(started, fallback=False)
                            return parsed
                        except json.JSONDecodeError:
                            self._record_call(started, fallback=False)
                            return text
                self._record_call(started, fallback=False)
                return None
        except Exception:
            log.warning("MCP call %s failed; using bounded fallback", name, exc_info=True)
            result = await self._fallback(name, arguments)
            self._record_call(started, fallback=True)
            return result

    async def _fallback(self, name: str, arguments: dict[str, Any]) -> Any:
        if name == "risk_scan":
            return await self.backend.risk_scan(str(arguments.get("text", "")))
        if name == "get_user_context":
            context = await self.backend.context(
                str(arguments["username"]), int(arguments["session_id"])
            )
            return context.model_dump()
        if name == "get_dashboard":
            return await self.backend.dashboard(str(arguments["username"]))
        if name == "search_knowledge":
            result = await self.rag.search_with_metrics(
                str(arguments["query"]),
                arguments.get("category"),
                int(arguments.get("limit", self.settings.rag_final_k)),
            )
            return result.model_dump(by_alias=True)
        if name == "persist_turn":
            compression = MemoryCompression.model_validate(arguments.get("compression") or {})
            return await self.backend.persist_turn(
                username=str(arguments["username"]),
                session_id=int(arguments["session_id"]),
                user_message=str(arguments["user_message"]),
                assistant_reply=str(arguments["assistant_reply"]),
                compression=compression,
            )
        if name == "crisis_support":
            return await self.backend.crisis(
                str(arguments["username"]), str(arguments["message"])
            )
        raise RuntimeError(f"Unknown MCP tool: {name}")

    @staticmethod
    def _record_call(started: float, fallback: bool) -> None:
        telemetry = current_run()
        if telemetry is not None:
            telemetry.record_mcp(
                (time.perf_counter() - started) * 1000,
                fallback=fallback,
            )
