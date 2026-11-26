"""One typed capability registry shared by MCP and in-process fallback.

Transport does not define permissions. The controller owns permitted capability
names and injects identity; tools validate arguments before either transport.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from functools import lru_cache
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

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


class OwnedUser(Arguments):
    username: str = Field(min_length=1, max_length=128)


class Context(OwnedUser):
    session_id: int = Field(gt=0)


class Search(Arguments):
    query: str = Field(min_length=1, max_length=4000)
    category: str | None = Field(default=None, max_length=100)
    limit: int = Field(default=6, ge=1, le=10)


class MemorySearch(Search, OwnedUser):
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

    @property
    def is_read(self):
        return self.scope.split(":")[1:2] == ["read"]

    def input_schema(self):
        return self.arguments.model_json_schema()


CAPABILITIES = {
    "get_user_context": Capability(Context, 10),
    "search_web": Capability(WebSearch, 45, scope="web:read"),
    "search_knowledge": Capability(Search, 20, scope="knowledge:read"),
    "memory_search": Capability(MemorySearch, 25, scope="memory:read"),
    "get_support_resources": Capability(Arguments, 10, scope="resources:read"),
    "crisis_support": Capability(Crisis, 15, replay_safe=False),
    "persist_turn": Capability(Persist, 15, replay_safe=False),
}

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


def validate_arguments(name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    if name not in CAPABILITIES:
        raise ValueError(f"Unknown capability: {name}")
    return CAPABILITIES[name].arguments.model_validate(arguments).model_dump()


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
    if name == "search_web":
        if type(result.get("available")) is not bool or not isinstance(result.get("results"), list):
            raise ValueError("web search must return explicit availability and source results")
        if len(result["results"]) > 5 or any(not isinstance(r, dict) or not all(isinstance(r.get(k), str)
                for k in ("title", "url", "snippet")) for r in result["results"]):
            raise ValueError("invalid web search results")
    if name == "persist_turn" and result.get("saved") is not True:
        raise ValueError("turn persistence was not confirmed")
    if name == "get_user_context":
        return AgentContext.model_validate(result).model_dump(by_alias=True, mode="json")
    if name == "search_knowledge":
        return RagSearchResult.model_validate(result).model_dump(by_alias=True)
    if name == "memory_search":
        return MemorySearchResult.model_validate(result).model_dump()
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
        if name == "get_user_context":
            result = await self.backend.context(**args)
            return result.model_dump(by_alias=True, mode="json")
        if name == "search_web":
            return await self.web_search.search(**args)
        if name == "search_knowledge":
            result = await self.rag.search_with_metrics(
                args["query"], args["category"], args["limit"]
            )
            return result.model_dump(by_alias=True)
        if name == "memory_search":
            if self.memory is None:
                raise RuntimeError("memory subsystem not configured")
            scope = {}
            if args["recent_messages"]:
                scope["recent_messages"] = True
            if args["time_window"]:
                scope["time_window"] = args["time_window"]
            return await self.memory.retrieve(args["username"], args["query"], args["limit"], **scope)
        if name == "get_support_resources":
            resources = normalize_result(name, await self.backend.support_resources())
            return [item for item in resources if not item["urgent"]]
        if name == "crisis_support":
            return await self.backend.crisis(args["username"], args["message"])
        if name == "persist_turn":
            args["compression"] = MemoryCompression.model_validate(args["compression"])
            return await self.backend.persist_turn(**args)
        raise ValueError(f"Unknown capability: {name}")
