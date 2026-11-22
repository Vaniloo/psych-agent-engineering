"""One typed capability registry shared by MCP and in-process fallback.

Transport does not define permissions. The controller owns permitted capability
names and injects identity; tools validate arguments before either transport.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from functools import lru_cache
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from .report_context import dashboard_with_availability
from .schemas import (
    AgentContext,
    HelpResource,
    MemoryCompression,
    MemorySearchResult,
    RagSearchResult,
)


class Arguments(BaseModel):
    model_config = ConfigDict(extra="forbid")


class WebSearch(Arguments):
    query: str = Field(min_length=1, max_length=240)
    limit: int = Field(default=5, ge=1, le=5)
    refresh: bool = False


class RoleVoiceSearch(Arguments):
    name: str = Field(min_length=1, max_length=80)
    focus: Literal["dialogue", "tone"] = "dialogue"
    limit: int = Field(default=5, ge=1, le=5)
    refresh: bool = False


class Scan(Arguments):
    text: str = Field(max_length=4000)


class OwnedUser(Arguments):
    username: str = Field(min_length=1, max_length=128)


class Context(OwnedUser):
    session_id: int = Field(gt=0)


class Search(Arguments):
    query: str = Field(min_length=1, max_length=4000)
    category: str | None = Field(default=None, max_length=100)
    limit: int = Field(default=6, ge=1, le=10)


class MemorySearch(Search, OwnedUser):
    purpose: Literal["lookup", "context"] = "lookup"
    session_id: int | None = Field(default=None, gt=0)
    time_window: Literal["today"] | None = None
    recent_messages: bool = False


class Crisis(OwnedUser):
    message: str = Field(min_length=1, max_length=4000)


class Persist(Context):
    request_id: str = Field(min_length=1, max_length=128)
    user_message: str = Field(min_length=1, max_length=4000)
    assistant_reply: str = Field(max_length=120000)
    compression: MemoryCompression = Field(default_factory=MemoryCompression)


@dataclass(frozen=True)
class Capability:
    arguments: type[Arguments]
    timeout_seconds: float
    replay_safe: bool = True
    scope: str = "system"
    description: str = ""
    label: str = ""
    routes: tuple[str, ...] = ()
    schema: dict | None = None
    handler: Callable[[dict], Awaitable[Any]] | None = None
    bindings: tuple[tuple[str, str], ...] = ()
    users: tuple[str, ...] = ()
    source: str = "builtin"

    @property
    def is_read(self):
        return self.scope.split(":")[1:2] == ["read"]

    def input_schema(self):
        import copy
        return copy.deepcopy(self.schema if self.schema is not None else self.arguments.model_json_schema())


CAPABILITIES = {
    "risk_scan": Capability(Scan, 5),
    "get_user_context": Capability(Context, 10),
    "search_web": Capability(WebSearch, 45, scope="web:read"),
    "search_role_voice": Capability(RoleVoiceSearch, 45, scope="web:read:role"),
    "search_knowledge": Capability(Search, 20, scope="knowledge:read"),
    "memory_search": Capability(MemorySearch, 25, scope="memory:read"),
    "get_dashboard": Capability(OwnedUser, 10, scope="reports:read"),
    "get_support_resources": Capability(Arguments, 10, scope="resources:read"),
    "crisis_support": Capability(Crisis, 15, replay_safe=False),
    "persist_turn": Capability(Persist, 15, replay_safe=False),
}

# Built-in transport docstrings remain compatible; extensions carry their own
# description/schema and never need a new trained tool ID or dispatch branch.
def tool_description(name):
    capability = CAPABILITIES[name]
    if capability.description:
        return capability.description
    return _builtin_descriptions().get(name) or name


@lru_cache(maxsize=1)
def _builtin_descriptions():
    import ast
    from pathlib import Path
    tree = ast.parse(Path(__file__).with_name("mcp_server.py").read_text())
    return {n.name: ast.get_docstring(n) for n in tree.body if isinstance(n, ast.AsyncFunctionDef)}


def register_read(name: str, capability: Capability):
    import re

    from jsonschema import Draft202012Validator
    from jsonschema.exceptions import SchemaError
    if (not re.fullmatch(r"[A-Za-z][\w-]{0,63}", name) or name in CAPABILITIES
            or not capability.is_read or not capability.replay_safe or not capability.handler
            or not capability.description.strip() or not 0 < capability.timeout_seconds <= 30):
        raise ValueError("invalid or duplicate read registration")
    schema = capability.input_schema()
    def check_refs(value):
        if isinstance(value, dict):
            for key, child in value.items():
                if key in {"$ref", "$dynamicRef"} and (not isinstance(child, str) or not child.startswith("#")):
                    raise ValueError("remote schema references are not supported")
                check_refs(child)
        elif isinstance(value, list):
            for child in value:
                check_refs(child)
    check_refs(schema)
    try:
        Draft202012Validator.check_schema(schema)
    except SchemaError as exc:
        raise ValueError("invalid read input schema") from exc
    if schema.get("type") != "object":
        raise ValueError("read arguments must be an object")
    CAPABILITIES[name] = capability


def validate_arguments(name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    if name not in CAPABILITIES:
        raise ValueError(f"Unknown capability: {name}")
    capability = CAPABILITIES[name]
    if capability.schema is not None:
        from jsonschema import Draft202012Validator

        # A schema must never fetch remote references during validation.
        from referencing import Registry
        try:
            Draft202012Validator(capability.schema, registry=Registry()).validate(arguments)
        except Exception as exc:
            raise ValueError("tool arguments do not match registered schema") from exc
        return dict(arguments)
    return capability.arguments.model_validate(arguments).model_dump()


def normalize_result(name: str, result: Any) -> Any:
    if name == "get_support_resources":
        # FastMCP wraps list return types in a structured "result" field.
        if isinstance(result, dict) and set(result) == {"result"}:
            result = result["result"]
        if not isinstance(result, list):
            raise TypeError("support resources must be a list")
        return [HelpResource.model_validate(item).model_dump() for item in result]
    if not isinstance(result, dict):
        raise TypeError(f"{name} must return an object")
    if name in {"search_web", "search_role_voice"}:
        if type(result.get("available")) is not bool or not isinstance(result.get("results"), list):
            raise ValueError("web search must return explicit availability and source results")
        if len(result["results"]) > 5 or any(not isinstance(r, dict) or not all(isinstance(r.get(k), str)
                for k in ("title", "url", "snippet")) for r in result["results"]):
            raise ValueError("invalid web search results")
    if name == "risk_scan" and type(result.get("highRisk", result.get("high_risk"))) is not bool:
        raise ValueError("risk scan must return a boolean highRisk")
    if name == "persist_turn" and result.get("saved") is not True:
        raise ValueError("turn persistence was not confirmed")
    if name == "get_user_context":
        return AgentContext.model_validate(result).model_dump(by_alias=True, mode="json")
    if name == "search_knowledge":
        return RagSearchResult.model_validate(result).model_dump(by_alias=True)
    if name == "memory_search":
        return MemorySearchResult.model_validate(result).model_dump()
    if name == "get_dashboard" and not isinstance(
        result.get("reportSummaryResponse", result.get("reports")), list
    ):
        raise ValueError("dashboard must contain reportSummaryResponse")
    if name == "get_dashboard":
        return dashboard_with_availability(result)
    if name == "crisis_support":
        if not isinstance(result.get("reply"), str) or not result["reply"].strip():
            raise ValueError("crisis response is missing")
        result["resources"] = [
            HelpResource.model_validate(item).model_dump() for item in result.get("resources", [])
        ]
    return result


class CapabilityExecutor:
    def __init__(self, backend, rag, memory=None, settings=None):
        from .web_search import WebSearchService
        self.backend, self.rag, self.memory = backend, rag, memory
        self.web_search = WebSearchService(settings)

    async def execute(self, name: str, arguments: dict[str, Any]) -> Any:
        args = validate_arguments(name, arguments)
        async with asyncio.timeout(CAPABILITIES[name].timeout_seconds):
            result = await self._dispatch(name, args)
        return normalize_result(name, result)

    async def _dispatch(self, name: str, args: dict[str, Any]) -> Any:
        if CAPABILITIES[name].handler is not None:
            return await CAPABILITIES[name].handler(args)
        if name == "risk_scan":
            return await self.backend.risk_scan(args["text"])
        if name == "get_user_context":
            result = await self.backend.context(**args)
            return result.model_dump(by_alias=True, mode="json")
        if name == "search_web":
            return await self.web_search.search(**args)
        if name == "search_role_voice":
            return await self.web_search.search_role_voice(**args)
        if name == "search_knowledge":
            result = await self.rag.search_with_metrics(
                args["query"], args["category"], args["limit"]
            )
            return result.model_dump(by_alias=True)
        if name == "memory_search":
            if self.memory is None:
                raise RuntimeError("memory subsystem not configured")
            if args["purpose"] == "context":
                if args["session_id"] is None:
                    raise ValueError("cross-session context requires the current owned session")
                return await self.memory.retrieve_context(
                    args["username"], args["query"], args["session_id"], min(args["limit"], 3)
                )
            scope = {}
            if args["recent_messages"]:
                scope["recent_messages"] = True
            if args["time_window"]:
                scope["time_window"] = args["time_window"]
            return await self.memory.retrieve(args["username"], args["query"], args["limit"], **scope)
        if name == "get_dashboard":
            return await self.backend.dashboard(args["username"])
        if name == "get_support_resources":
            resources = normalize_result(name, await self.backend.support_resources())
            return [item for item in resources if not item["urgent"]]
        if name == "crisis_support":
            return await self.backend.crisis(args["username"], args["message"])
        if name == "persist_turn":
            args["compression"] = MemoryCompression.model_validate(args["compression"])
            return await self.backend.persist_turn(**args)
        raise ValueError(f"Unknown capability: {name}")
