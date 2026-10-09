from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any

from .backend_client import BackendClient
from .capabilities import CAPABILITIES, CapabilityExecutor, normalize_result, validate_arguments
from .config import Settings
from .rag import RagEngine
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
        self.executor = CapabilityExecutor(backend, rag)

    async def call(self, name: str, arguments: dict[str, Any]) -> Any:
        arguments = validate_arguments(name, arguments)
        capability = CAPABILITIES[name]
        dispatched = False
        started = time.perf_counter()
        try:
            from mcp import ClientSession
            from mcp.client.streamable_http import streamable_http_client

            async with asyncio.timeout(capability.timeout_seconds), streamable_http_client(self.settings.mcp_server_url) as (
                read_stream,
                write_stream,
                _,
            ), ClientSession(read_stream, write_stream) as session:
                await session.initialize()
                dispatched = True
                result = await session.call_tool(name, arguments=arguments)
                if getattr(result, "isError", False) or getattr(result, "is_error", False):
                    raise RuntimeError(f"MCP tool failed: {name}")
                structured = getattr(result, "structuredContent", None) or getattr(
                    result, "structured_content", None
                )
                if structured is not None:
                    self._record_call(started, fallback=False)
                    return normalize_result(name, structured)
                for item in getattr(result, "content", []):
                    text = getattr(item, "text", None)
                    if text:
                        try:
                            parsed = json.loads(text)
                            self._record_call(started, fallback=False)
                            return normalize_result(name, parsed)
                        except json.JSONDecodeError:
                            self._record_call(started, fallback=False)
                            return normalize_result(name, text)
                self._record_call(started, fallback=False)
                raise ValueError(f"Empty tool result: {name}")
        except Exception:
            if dispatched and not capability.replay_safe:
                # The server may have committed before the connection failed.
                # Do not replay writes without an end-to-end idempotency key.
                self._record_call(started, fallback=False)
                raise
            log.warning("MCP call %s failed; using bounded fallback", name, exc_info=True)
            async with asyncio.timeout(capability.timeout_seconds):
                result = await self._fallback(name, arguments)
            self._record_call(started, fallback=True)
            return normalize_result(name, result)

    async def _fallback(self, name: str, arguments: dict[str, Any]) -> Any:
        return await self.executor.execute(name, arguments)

    @staticmethod
    def _record_call(started: float, fallback: bool) -> None:
        telemetry = current_run()
        if telemetry is not None:
            telemetry.record_mcp(
                (time.perf_counter() - started) * 1000,
                fallback=fallback,
            )
