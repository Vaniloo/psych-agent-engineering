"""Host-granted MCP reads: discovery -> candidates -> validated arguments -> execution.

Server annotations describe a tool, but never grant permission. The private host
manifest grants individual reads, routes, account scope and identity bindings.
"""

import asyncio
import json
import logging
import os
import time
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import urlsplit

import httpx
from pydantic import BaseModel, ConfigDict, Field

from .capabilities import CAPABILITIES, Arguments, Capability, register_read
from .mcp_result import decode_result

log = logging.getLogger(__name__)
BOUND = {"username", "session_id", "request_id", "model_approval"}
ROUTES = {"chat", "clarify", "knowledge", "strategy", "memory", "dashboard"}


@asynccontextmanager
async def mcp_session(url, headers=None):
    from mcp import ClientSession
    from mcp.client.streamable_http import streamable_http_client

    async with (
        httpx.AsyncClient(headers=headers, trust_env=False, timeout=15) as client,
        streamable_http_client(url, http_client=client) as (read, write, _),
        ClientSession(read, write) as session,
    ):
        await session.initialize()
        yield session


class ReadGrant(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(pattern=r"^[A-Za-z][\w-]{0,39}$")
    label: str = Field(min_length=1, max_length=80)
    routes: list[str] = Field(min_length=1, max_length=6)
    bindings: dict[str, str] = Field(default_factory=dict)
    users: list[str] = Field(default_factory=list, max_length=100)
    timeout_seconds: float = Field(default=10, gt=0, le=30)


class ServerGrant(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str = Field(pattern=r"^[A-Za-z][\w-]{0,19}$")
    url: str
    token_env: str = ""
    tools: list[ReadGrant] = Field(min_length=1, max_length=16)


class Manifest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    servers: list[ServerGrant] = Field(max_length=8)


def unpack_result(result):
    data = decode_result(result, max_text_chars=120000)
    if isinstance(data, str):
        data = {"text": data}
    if len(json.dumps(data, ensure_ascii=False)) > 120000:
        raise ValueError("MCP result exceeds response budget")
    return data if isinstance(data, dict) else {"result": data}


class DynamicReadCatalog:
    def __init__(self, path=""):
        self.path = path
        self.names = set()
        self.lock = asyncio.Lock()
        self.next_refresh = 0.0
        self.status = "disabled" if not path else "pending"

    def info(self):
        return {
            "status": self.status,
            "tools": sorted(self.names),
            "refreshSeconds": 60,
            "permission": "host_granted_reads_only",
        }

    async def refresh(self, force=False):
        if not self.path or not force and time.monotonic() < self.next_refresh:
            return
        async with self.lock:
            if not force and time.monotonic() < self.next_refresh:
                return
            self.next_refresh = time.monotonic() + 60
            discovered = {}
            failed = False
            try:
                path = Path(self.path)
                if path.stat().st_size > 128000:
                    raise ValueError("manifest exceeds budget")
                manifest = Manifest.model_validate_json(path.read_text())
                if len({s.id for s in manifest.servers}) != len(manifest.servers):
                    raise ValueError("duplicate MCP namespace")

                async def discover(server):
                    try:
                        async with asyncio.timeout(5):
                            return await self._discover(server), False
                    except Exception:  # noqa: BLE001 - one remote plugin cannot fail the conversation
                        # No URL, secret, tool arguments or user content in this log.
                        log.warning("MCP read discovery unavailable for namespace %s", server.id)
                        return {}, True

                for rows, error in await asyncio.gather(*(discover(s) for s in manifest.servers)):
                    discovered.update(rows)
                    failed |= error
            except Exception:  # noqa: BLE001 - malformed host configuration disables extensions only
                failed = True
                log.warning("MCP host read manifest unavailable or invalid")
            # Publish an atomic snapshot between awaits. Removed/unavailable tools
            # cannot silently remain selectable with stale schemas.
            for name in self.names:
                CAPABILITIES.pop(name, None)
            self.names.clear()
            for name, capability in discovered.items():
                try:
                    register_read(name, capability)
                    self.names.add(name)
                except ValueError:
                    failed = True
            self.status = "degraded" if failed else "ready"

    async def _discover(self, server):
        parsed = urlsplit(server.url)
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError("invalid configured MCP endpoint")
        key = os.environ.get(server.token_env, "") if server.token_env else ""
        if server.token_env and not key:
            raise ValueError("configured MCP credential missing")
        headers = {"Authorization": "Bearer " + key} if key else {}
        tools = {}
        async with mcp_session(server.url, headers) as session:
            cursor = None
            for _ in range(4):
                page = await session.list_tools(cursor=cursor)
                for tool in page.tools:
                    if tool.name in tools or len(tools) >= 128:
                        raise ValueError("duplicate or oversized MCP tool catalog")
                    tools[tool.name] = tool
                cursor = page.nextCursor
                if not cursor:
                    break
            if cursor:
                raise ValueError("MCP catalog exceeds discovery budget")
        found = {}
        for grant in server.tools:
            tool = tools.get(grant.name)
            if tool is None or not tool.annotations or tool.annotations.readOnlyHint is not True:
                continue
            # MCP destructiveHint is meaningful only for non-read tools. A
            # host grant is still required; annotations alone confer no access.
            if (
                not set(grant.routes) <= ROUTES
                or "*" in grant.users
                or not set(grant.bindings.values())
                <= {"message", "username", "session_id", "request_id"}
            ):
                raise ValueError("invalid host read policy")
            schema = {**tool.inputSchema, "additionalProperties": False}
            if (
                schema.get("type") != "object"
                or not tool.description
                or len(json.dumps(schema)) > 16000
                or len(tool.description) > 4000
            ):
                raise ValueError("unbounded tool descriptor")
            props = schema.get("properties", {})
            if "model_approval" in props or not set(grant.bindings) <= set(props):
                raise ValueError("invalid identity or approval schema")
            bindings = {k: k for k in BOUND & set(props)}
            bindings.update(grant.bindings)
            if any(bindings.get(k) != k for k in BOUND & set(props)):
                raise ValueError("identity binding cannot be overridden")
            name = server.id + "__" + tool.name
            if name in found or name in CAPABILITIES and name not in self.names:
                raise ValueError("tool namespace collision")

            async def handler(args, url=server.url, headers=headers, remote_name=tool.name):
                async with mcp_session(url, headers) as session:
                    return unpack_result(await session.call_tool(remote_name, arguments=args))

            found[name] = Capability(
                Arguments,
                grant.timeout_seconds,
                scope="extension:read",
                description=tool.description,
                label=grant.label,
                routes=tuple(grant.routes),
                schema=schema,
                handler=handler,
                bindings=tuple(bindings.items()),
                users=tuple(grant.users),
                source="mcp:" + server.id,
            )
        return found


def authorized_extensions(route, owner):
    return [
        name
        for name, cap in CAPABILITIES.items()
        if cap.handler is not None
        and cap.is_read
        and route in cap.routes
        and (not cap.users or owner in cap.users)
    ]


def bound_arguments(name, state):
    return {field: state[source] for field, source in CAPABILITIES[name].bindings}


async def resolve_arguments(name, state, generation):
    from .dynamic_read_review import review_action

    result = await review_action([name], state, generation, preferred=name)
    if result["tool"] != name:
        raise ValueError("selected tool is unnecessary or needs clarification")
    return result["arguments"]
