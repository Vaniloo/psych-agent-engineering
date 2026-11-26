"""Tools the agent model can call.

Each tool declares only the arguments the model is allowed to choose. Identity
(username, session) is bound by the host from the authenticated request, so a
model can never read another account. Execution goes through the capability
layer (validation, timeout, local/MCP transport, telemetry, activity events).
"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from .config import Settings


class ToolArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")


class KnowledgeArgs(ToolArgs):
    query: str = Field(min_length=1, max_length=400,
                       description="检索词。用简洁的中文关键词或问题，不要包含用户隐私。")


class MemoryArgs(ToolArgs):
    query: str = Field(min_length=1, max_length=400,
                       description="要在用户自己过去说过的话里查找的内容，例如“喜欢的放松方式”。")
    time_range: Literal["any", "today"] = Field(
        default="any", description="只查今天的话用 today，否则用 any。")


class WebArgs(ToolArgs):
    query: str = Field(min_length=1, max_length=240,
                       description="公开搜索词，只含外部主题和用户明确给出的地点等参数，不含隐私。")


class NoArgs(ToolArgs):
    pass


@dataclass(frozen=True)
class Turn:
    """Who is asking. Bound by the host, never taken from model output."""
    username: str
    session_id: int


@dataclass(frozen=True)
class Tool:
    name: str
    description: str
    args: type[ToolArgs]
    capability: str
    bind: Callable[[ToolArgs, Turn], dict[str, Any]]
    summarize: Callable[[Any], Any]
    max_calls: int = 2  # per turn; for knowledge this means "search, then at most one rewrite"

    def openai_schema(self) -> dict:
        return {"type": "function", "function": {
            "name": self.name, "description": self.description,
            "parameters": self.parameters()}}

    def parameters(self) -> dict:
        schema = self.args.model_json_schema()
        schema.pop("title", None)
        for prop in schema.get("properties", {}).values():
            prop.pop("title", None)
        return schema


EVIDENCE_HINTS = {
    "strong": "",
    "weak": "这些片段与问题的相关度偏低。可以换一个更具体或不同角度的检索词再查一次；仍不足就如实说明资料有限。",
    "none": "没有检索到相关资料。可以换一个检索词再查一次；仍没有就如实说明知识库里没有找到。",
}


def _knowledge_summary(result: dict) -> dict:
    """Give the model the evidence and how far to trust it, so it can decide to re-query."""
    hits = result.get("hits", []) if isinstance(result, dict) else []
    evidence = ((result or {}).get("metrics") or {}).get("evidence") or ("strong" if hits else "none")
    summary = {"evidence": evidence,
               "hits": [{"id": h.get("id"), "source": h.get("source"), "confidence": h.get("confidence_label"),
                         "content": str(h.get("content", ""))[:600]} for h in hits[:5]]}
    if hint := EVIDENCE_HINTS.get(evidence):
        summary["hint"] = hint
    return summary


def _memory_summary(result: dict) -> dict:
    rows = result.get("evidence", []) if isinstance(result, dict) else []
    return {"records": [{"content": r.get("content"), "observedAt": r.get("observedAt"),
                         "sessionId": r.get("sessionId")} for r in rows[:6]]}


def _web_summary(result: dict) -> dict:
    if not isinstance(result, dict) or not result.get("available"):
        return {"available": False, "reason": (result or {}).get("reason", "unavailable")}
    return {"available": True, "results": [
        {"title": r["title"], "url": r["url"], "snippet": r["snippet"][:300]}
        for r in result.get("results", [])[:5]]}


def _resources_summary(result: list) -> dict:
    return {"resources": [{k: r.get(k) for k in ("name", "contact", "description")}
                          for r in (result or [])]}


SEARCH_KNOWLEDGE = Tool(
    name="search_knowledge",
    description="检索心理健康知识库（NIMH 等公开机构资料）。需要解释心理概念、提供有依据的应对方法时使用。"
                "结果不够相关时，可以换一个说法再查一次。",
    args=KnowledgeArgs, capability="search_knowledge",
    bind=lambda a, t: {"query": a.query, "limit": 5},
    summarize=_knowledge_summary,
)
RECALL_MEMORY = Tool(
    name="recall_memory",
    description="查找当前用户在过去对话里亲口说过的话。用户问起自己以前的经历、偏好，"
                "或你需要了解他之前提过的背景时使用。只返回用户本人的原话和时间。",
    args=MemoryArgs, capability="memory_search",
    bind=lambda a, t: {"username": t.username, "query": a.query, "limit": 6,
                       "recent_messages": True,
                       "time_window": "today" if a.time_range == "today" else None},
    summarize=_memory_summary,
)
SEARCH_WEB = Tool(
    name="search_web",
    description="搜索公开网页，返回标题、链接和摘要。只在需要实时或外部信息（天气、新闻、官方文档）时使用。"
                "缺少地点等必要信息时先问用户。",
    args=WebArgs, capability="search_web",
    bind=lambda a, t: {"query": a.query, "limit": 5},
    summarize=_web_summary,
)
SUPPORT_RESOURCES = Tool(
    name="get_support_resources",
    description="获取已核实的专业心理援助资源（热线等）。用户需要专业帮助或表达严重困扰时使用。",
    args=NoArgs, capability="get_support_resources",
    bind=lambda a, t: {},
    summarize=_resources_summary,
    max_calls=1,
)

ALL_TOOLS = {t.name: t for t in (SEARCH_KNOWLEDGE, RECALL_MEMORY, SEARCH_WEB, SUPPORT_RESOURCES)}


def web_allowed(settings: Settings, username: str) -> bool:
    users = {u.strip() for u in settings.web_search_users.split(",") if u.strip() and u.strip() != "*"}
    return settings.web_search_enabled and username in users


def available_tools(settings: Settings, username: str) -> list[Tool]:
    """The host decides which tools exist for this account; the model chooses among them."""
    return [t for t in ALL_TOOLS.values() if t.name != "search_web" or web_allowed(settings, username)]


class ToolError(ValueError):
    pass


def parse_arguments(tool: Tool, raw: str) -> ToolArgs:
    try:
        data = json.loads(raw or "{}")
    except json.JSONDecodeError as exc:
        raise ToolError("arguments are not valid JSON") from exc
    if not isinstance(data, dict):
        raise ToolError("arguments must be a JSON object")
    try:
        return tool.args.model_validate(data)
    except ValidationError as exc:
        raise ToolError(exc.errors(include_url=False)[0]["msg"]) from exc


async def run_tool(tool: Tool, args: ToolArgs, turn: Turn,
                   invoke: Callable[[str, dict], Awaitable[Any]]) -> tuple[str, Any]:
    """Execute and return (status, raw result). Failures become observations, not crashes."""
    try:
        result = await invoke(tool.capability, tool.bind(args, turn))
    except Exception:  # noqa: BLE001 - the model is told the tool failed and can adapt
        return "error", {"unavailable": True}
    if isinstance(result, dict) and (result.get("unavailable") or result.get("available") is False):
        return "unavailable", result
    return "ok", result
