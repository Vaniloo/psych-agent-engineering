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
        memory=None,
    ) -> None:
        self.settings = settings
        self.backend = backend
        self.rag = rag
        self.executor = CapabilityExecutor(backend, rag, memory)

    async def call(self, name: str, arguments: dict[str, Any]) -> Any:
        started, status = time.perf_counter(), "error"
        telemetry = current_run()
        fallback_before = telemetry.fallback_count if telemetry is not None else 0
        try:
            result = await self._call(name, arguments)
            status = (
                "no_change"
                if isinstance(result, dict)
                and (result.get("created") is False or result.get("applied") is False)
                else "ok"
            )
            return result
        finally:
            telemetry = current_run()
            if telemetry is not None:
                telemetry.record_capability(
                    name,
                    (time.perf_counter() - started) * 1000,
                    "mcp→local"
                    if telemetry.fallback_count > fallback_before
                    else self.settings.tool_transport,
                    status,
                )

    async def _call(self, name: str, arguments: dict[str, Any]) -> Any:
        arguments = validate_arguments(name, arguments)
        capability = CAPABILITIES[name]
        if self.settings.tool_transport == "local":
            return await self.executor.execute(name, arguments)
        if self.settings.tool_transport != "mcp":
            raise ValueError("TOOL_TRANSPORT must be local or mcp")
        dispatched = False
        started = time.perf_counter()
        try:
            from mcp import ClientSession
            from mcp.client.streamable_http import streamable_http_client

            async with (
                asyncio.timeout(capability.timeout_seconds),
                streamable_http_client(
                    self.settings.mcp_server_url,
                    headers={"X-Internal-Service-Token": self.settings.internal_service_token},
                ) as (
                    read_stream,
                    write_stream,
                    _,
                ),
                ClientSession(read_stream, write_stream) as session,
            ):
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
                # Crisis writes do not have end-to-end idempotency. Turn saves
                # do, but automatic replay remains disabled for both writes.
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
