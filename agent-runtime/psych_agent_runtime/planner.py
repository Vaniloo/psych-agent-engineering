from __future__ import annotations

import json
import logging
from typing import ClassVar

import httpx

from .config import Settings
from .decision_router import DecisionRouter
from .jev_decisions import JevDecisions
from .ollama_client import OllamaClient
from .route_policy import plan_for_route
from .schemas import AgentContext, Plan

log = logging.getLogger(__name__)


class AgentPlanner:
    ALLOWED_TOOLS: ClassVar[set[str]] = {"search_knowledge", "get_dashboard"}

    def __init__(self, ollama: OllamaClient, settings: Settings | None = None) -> None:
        self.ollama = ollama
        self.settings = settings or Settings()
        if self.settings.router_provider not in {"legacy", "laya", "jev"}:
            raise ValueError("ROUTER_PROVIDER must be legacy, laya or jev")
        self.router = (JevDecisions(self.settings) if self.settings.router_provider == "jev"
                       else DecisionRouter(self.settings) if self.settings.router_provider == "laya"
                       else None)

    async def close(self) -> None:
        if self.router:
            await self.router.close()

    async def plan(self, message: str, context: AgentContext | None = None) -> Plan:
        if self.router is None:
            return await self._legacy_plan(message)
        try:
            decision = await self.router.decide(
                message,
                [item.model_dump() for item in context.messages] if context else [],
                context.session_summary if context else "",
            )
            if decision["accepted"]:
                return plan_for_route(decision.get("effectiveRoute", decision["route"]),
                                      message, "结构化决策模型选择", decision)
            reason = "low_probability_or_uncalibrated"
        except (httpx.HTTPError, ValueError, KeyError, TypeError) as exc:
            log.warning("Decision router unavailable (%s); using bounded fallback", type(exc).__name__)
            decision = {"provider": self.settings.router_provider, "accepted": False}
            reason = "router_unavailable_or_invalid"
        # A failed or uncertain router cannot silently issue tool calls. Explicit
        # fallback uses the existing planner, with its result recorded separately.
        fallback = await self._legacy_plan(message)
        fallback.routing = {**decision, "fallback": reason, "effectiveRoute": fallback.intent}
        return fallback

    async def assess_risk(self, message: str, context: AgentContext) -> dict:
        if not isinstance(self.router, JevDecisions):
            return {"provider": self.settings.router_provider, "riskBand": "unknown",
                    "accepted": False, "fallback": "risk_head_not_trained_or_configured"}
        try:
            return await self.router.assess_risk(
                message, [item.model_dump() for item in context.messages], context.session_summary,
            )
        except (httpx.HTTPError, ValueError, TypeError, KeyError):
            return {"provider": "jev", "riskBand": "unknown", "accepted": False,
                    "fallback": "risk_decision_unavailable_or_invalid"}

    async def _legacy_plan(self, message: str) -> Plan:
        deterministic = self._deterministic_plan(message)
        if deterministic.intent != "chat":
            return deterministic
        try:
            result = await self.ollama.chat_json(
                [
                    {
                        "role": "system",
                        "content": (
                            "你是心理支持 Agent 的规划节点。只返回 JSON。"
                            "intent 只能是 chat、knowledge、dashboard、memory、strategy。"
                            "tools 是数组，每项格式为 "
                            '{"name":"search_knowledge|get_dashboard","arguments":{}}。'
                            "知识解释、心理困扰、自助练习优先 search_knowledge；"
                            "统计和报告使用 get_dashboard；普通寒暄不调用工具。"
                        ),
                    },
                    {"role": "user", "content": message},
                ],
                temperature=0.0,
                purpose="planner",
            )
            tools = [
                item
                for item in result.get("tools", [])
                if isinstance(item, dict) and item.get("name") in self.ALLOWED_TOOLS
            ]
            intent = str(result.get("intent", "chat"))
            if intent not in {"chat", "knowledge", "dashboard", "memory", "strategy"}:
                intent = "chat"
            # Discard generated arguments and rebuild a bounded capability plan.
            if any(item["name"] == "get_dashboard" for item in tools):
                intent = "dashboard"
            elif any(item["name"] == "search_knowledge" for item in tools):
                intent = "strategy" if intent == "strategy" else "knowledge"
            return plan_for_route(intent, message, str(result.get("reason", "")))
        except Exception:
            log.warning("Planner model failed; continuing with deterministic chat plan", exc_info=True)
            return deterministic

    def _deterministic_plan(self, message: str) -> Plan:
        compact = message.strip().lower()
        if any(term in compact for term in ("报告", "dashboard", "风险统计", "情绪趋势")):
            return Plan(
                intent="dashboard",
                tools=[{"name": "get_dashboard", "arguments": {}}],
                reason="命中报告/统计意图",
            )
        if any(term in compact for term in ("记得我", "我的偏好", "以前聊过", "我的画像")):
            return Plan(intent="memory", tools=[], reason="由已加载的用户上下文直接回答")
        if any(
            term in compact
            for term in (
                "怎么办",
                "怎么缓解",
                "如何",
                "原因",
                "焦虑",
                "失眠",
                "压力",
                "低落",
                "惊恐",
                "练习",
            )
        ):
            return Plan(
                intent="knowledge",
                tools=[
                    {
                        "name": "search_knowledge",
                        "arguments": {"query": message, "limit": 6},
                    }
                ],
                reason="命中心理知识或行动建议意图",
            )
        return Plan(intent="chat", tools=[], reason="普通对话")

    @staticmethod
    def compact(plan: Plan) -> str:
        return json.dumps(plan.model_dump(), ensure_ascii=False, separators=(",", ":"))
