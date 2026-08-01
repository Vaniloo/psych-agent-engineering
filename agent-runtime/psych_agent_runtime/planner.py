from __future__ import annotations

import json
import logging
from typing import ClassVar

from .ollama_client import OllamaClient
from .schemas import Plan

log = logging.getLogger(__name__)


class AgentPlanner:
    ALLOWED_TOOLS: ClassVar[set[str]] = {"search_knowledge", "get_dashboard"}

    def __init__(self, ollama: OllamaClient) -> None:
        self.ollama = ollama

    async def plan(self, message: str) -> Plan:
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
            return Plan(intent=intent, tools=tools, reason=str(result.get("reason", "")))
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
