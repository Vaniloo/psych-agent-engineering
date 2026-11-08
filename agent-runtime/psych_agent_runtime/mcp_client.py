from __future__ import annotations

import asyncio
import logging
import time
import uuid
from typing import Any

from .backend_client import BackendClient
from .capabilities import CAPABILITIES, CapabilityExecutor, normalize_result, validate_arguments
from .config import Settings
from .rag import RagEngine
from .telemetry import current_run
from .tool_activity import PUBLIC_TOOLS, result_summary
from .dynamic_tools import DynamicReadCatalog
from .mcp_result import decode_result

log = logging.getLogger(__name__)


class McpToolClient:
    """MCP client with explicit local fallbacks for critical safety paths."""

    def __init__(
        self,
        settings: Settings,
        backend: BackendClient,
        rag: RagEngine,
        memory=None,
        emit=None,
    ) -> None:
        self.settings = settings
        self.backend = backend
        self.rag = rag
        self.executor = CapabilityExecutor(backend, rag, memory, settings)
        self.emit = emit
        self.catalog = DynamicReadCatalog(settings.mcp_read_tools_config)

    async def call(self, name: str, arguments: dict[str, Any]) -> Any:
        started, status = time.perf_counter(), "error"
        telemetry = current_run()
        fallback_before = telemetry.fallback_count if telemetry is not None else 0
        call_id = uuid.uuid4().hex
        result = None
        cap = CAPABILITIES.get(name)
        label = PUBLIC_TOOLS.get(name) or (cap.label if cap and cap.is_read else None)
        if label:
            self._emit_activity(
                {
                    "callId": call_id,
                    "name": name,
                    "label": label,
                    "registeredRead": bool(cap and cap.handler is not None and cap.is_read),
                    "status": "running",
                },
            )
        try:
            result = await self._call(name, arguments)
            status = (
                "no_change"
                if isinstance(result, dict)
                and (result.get("created") is False or result.get("applied") is False)
                else "ok"
            )
            if isinstance(result, dict) and result.get("unavailable"):
                status = "error"
            if cap and cap.handler is not None and isinstance(result, dict) and result.get("available") is False:
                status = "error"
            if name in {"advance_goal", "search_web", "search_role_voice"} and isinstance(result, dict) and result.get("available") is False:
                status = "error"
            return result
        finally:
            telemetry = current_run()
            duration_ms = (time.perf_counter() - started) * 1000
            transport = (
                "mcp→local"
                if telemetry is not None and telemetry.fallback_count > fallback_before
                else self.settings.tool_transport
            )
            if cap and cap.handler is not None:
                transport = "mcp" if cap.source.startswith("mcp:") else "local"
            if label:
                self._emit_activity(
                    {
                        "callId": call_id,
                        "name": name,
                        "label": label,
                        "registeredRead": bool(cap and cap.handler is not None and cap.is_read),
                        "status": status,
                        "durationMs": round(duration_ms, 2),
                        "transport": transport,
                        **({"sources": [{"title": r["title"], "url": r["url"]} for r in result.get("results", [])[:5]]}
                           if name in {"search_web", "search_role_voice"} and isinstance(result, dict) else {}),
                        **({"searchContext": {k: result['pipeline'][k] for k in (
                            'candidatePages', 'pagesRead', 'chunksSelected', 'selection', 'calibrated')
                            if k in result['pipeline']}}
                           if name in {"search_web", "search_role_voice"} and isinstance(result, dict)
                           and isinstance(result.get('pipeline'), dict) else {}),
                        "summary": result_summary(name, result)
                        if status != "error"
                        else "暂时无法完成调用，回复会说明这一限制",
                    },
                )
            if telemetry is not None:
                telemetry.record_capability(
                    name,
                    duration_ms,
                    transport,
                    status,
                )

    def _emit_activity(self, data):
        if self.emit:
            try:
                self.emit("tool_activity", data)
            except Exception:  # noqa: BLE001 - display delivery cannot fail a business write
                # Display delivery must never turn a committed write into a failure.
                log.warning("Tool activity delivery unavailable")

    async def _call(self, name: str, arguments: dict[str, Any]) -> Any:
        arguments = validate_arguments(name, arguments)
        capability = CAPABILITIES[name]
        if capability.handler is not None:
            # Registered transport owns its endpoint; local mode only controls
            # built-in tools. External reads have no executable local fallback.
            started = time.perf_counter()
            try:
                return await self.executor.execute(name, arguments)
            finally:
                if capability.source.startswith("mcp:"):
                    self._record_call(started, fallback=False)
        if self.settings.tool_transport == "local":
            return await self.executor.execute(name, arguments)
        if self.settings.tool_transport != "mcp":
            raise ValueError("TOOL_TRANSPORT must be local or mcp")
        dispatched = False
        started = time.perf_counter()
        try:
            from .dynamic_tools import mcp_session

            async with (
                asyncio.timeout(capability.timeout_seconds),
                mcp_session(
                    self.settings.mcp_server_url,
                    headers={"X-Internal-Service-Token": self.settings.internal_service_token},
                ) as session,
            ):
                dispatched = True
                result = await session.call_tool(name, arguments=arguments)
                decoded = normalize_result(name, decode_result(result))
                self._record_call(started, fallback=False)
                return decoded
        except Exception:
            if dispatched and not capability.replay_safe:
                # The server may have committed before the connection failed.
                # Crisis writes do not have end-to-end idempotency. Turn saves
                # do, but automatic replay remains disabled for both writes.
                self._record_call(started, fallback=False)
                raise
            log.warning("MCP call %s failed; using bounded fallback", name, exc_info=True)
            async with asyncio.timeout(capability.timeout_seconds):
                result = await self.executor.execute(name, arguments)
            self._record_call(started, fallback=True)
            return normalize_result(name, result)

    @staticmethod
    def _record_call(started: float, fallback: bool) -> None:
        telemetry = current_run()
        if telemetry is not None:
            telemetry.record_mcp(
                (time.perf_counter() - started) * 1000,
                fallback=fallback,
            )
