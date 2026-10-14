"""Bounded policy planner. Generative models do not plan executable tools."""

from __future__ import annotations

import logging

import httpx

from .config import Settings
from .decision_router import DecisionRouter
from .route_policy import guarantee_requested_reads, plan_for_route
from .schemas import AgentContext, Plan

log = logging.getLogger(__name__)


class AgentPlanner:
    def __init__(self, settings: Settings | None = None):
        self.settings = settings or Settings()
        if self.settings.router_provider not in {"rules", "laya", "qwen"}:
            raise ValueError("ROUTER_PROVIDER must be rules, laya or qwen")
        self.router = (
            DecisionRouter(self.settings)
            if self.settings.router_provider in {"laya", "qwen"}
            else None
        )

    async def close(self):
        if self.router:
            await self.router.close()

    async def plan(self, message: str, context: AgentContext | None = None) -> Plan:
        decision = {"provider": "rules", "accepted": False}
        if self.router:
            try:
                decision = await self.router.decide(
                    message,
                    [item.model_dump() for item in context.messages] if context else [],
                    context.session_summary if context else "",
                )
                if decision["accepted"]:
                    return guarantee_requested_reads(
                        plan_for_route(decision["route"], message, "本地结构化决策", decision),
                        message,
                        context,
                    )
                decision["fallback"] = "low_probability_or_uncalibrated"
            except (httpx.HTTPError, ValueError, TypeError, KeyError) as exc:
                log.warning("Local router unavailable: %s", type(exc).__name__)
                decision = {
                    "provider": self.settings.router_provider,
                    "accepted": False,
                    "fallback": "router_unavailable_or_invalid",
                }
        plan = self._deterministic_plan(message)
        decision["effectiveRoute"] = plan.intent
        plan.routing = decision
        return guarantee_requested_reads(plan, message, context)

    @staticmethod
    def _deterministic_plan(message: str) -> Plan:
        text = message.strip().lower()
        if any(term in text for term in ("报告", "dashboard", "风险统计", "情绪趋势")):
            route = "dashboard"
        elif any(term in text for term in ("记得我", "以前聊过", "我的画像", "我的偏好")):
            route = "memory"
        elif any(term in text for term in ("我的目标", "练习记录", "目标进度")) or any(
            term in text for term in ("制定计划", "具体步骤", "带我练习", "行动方案")
        ):
            route = "strategy"
        elif any(
            term in text
            for term in (
                "什么是",
                "解释一下",
                "原因",
                "怎么办",
                "怎么缓解",
                "焦虑",
                "失眠",
                "压力",
                "低落",
                "惊恐",
            )
        ):
            route = "knowledge"
        elif text in {"继续", "怎么办", "那个", "帮我处理一下"}:
            route = "clarify"
        else:
            route = "chat"
        return plan_for_route(route, message, "受限规则回退")
