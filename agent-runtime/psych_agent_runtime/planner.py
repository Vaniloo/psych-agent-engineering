"""Classify intent, then map the route to application-owned tool permissions."""

from __future__ import annotations

from .route_policy import guarantee_requested_reads, plan_for_route
from .schemas import AgentContext, Plan
from .support_decisions import classify_intent


class AgentPlanner:
    def __init__(self, client=None):
        self.client = client

    async def plan(self, message: str, context: AgentContext | None = None) -> Plan:
        labels, fallback = await classify_intent(self.client, message, context)
        route = labels.get("route")
        plan = plan_for_route(route, message, "DeepSeek 意图分类") if route else self._deterministic_plan(message)
        plan.routing = {"provider": "deepseek" if route else "rules", "accepted": bool(route),
                        "effectiveRoute": plan.intent, "conversationKind": labels.get("conversation_kind"),
                        "fallback": "" if route else fallback}
        if route:
            plan.routing["model"] = self.client.model
        return guarantee_requested_reads(plan, message, context)

    @staticmethod
    def _deterministic_plan(message: str) -> Plan:
        text = message.strip().lower()
        if any(term in text for term in ("报告", "dashboard", "风险统计", "情绪趋势")):
            route = "dashboard"
        elif any(term in text for term in ("记得我", "以前聊过", "我的画像", "我的偏好")):
            route = "memory"
        elif any(
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
        return plan_for_route(route, message, "规则路由")
