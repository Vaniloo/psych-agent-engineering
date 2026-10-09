"""Application-owned capability policies; model output never becomes tool arguments."""

from __future__ import annotations

from .schemas import Plan

RESPONSE_POLICIES = {
    "chat": "本轮以倾听和回应感受为主，用户未要求建议时不要主动堆砌行动清单。",
    "knowledge": "解释知识和适用边界，优先使用检索证据；引用不足时明确说明。",
    "memory": "仅依据已加载的真实记忆回答，不知道的经历不要推测，不泄露内部字段。",
    "dashboard": "仅总结实际返回的报告和统计；没有记录时明确说明，不推测趋势。",
    "strategy": "结合用户处境和知识证据，给出一到三个具体且容易开始的步骤。",
    "clarify": "先回应用户，再提出一个简短的澄清问题；不要猜测用户的经历或目标。",
}


def plan_for_route(route: str, message: str, reason: str, routing: dict | None = None) -> Plan:
    tools = []
    if route in {"knowledge", "strategy"}:
        tools = [{"name": "search_knowledge", "arguments": {"query": message, "limit": 6}}]
    elif route == "dashboard":
        tools = [{"name": "get_dashboard", "arguments": {}}]
    allowed = {
        "chat": [],
        "clarify": [],
        "knowledge": ["search_knowledge", "memory_search"],
        "strategy": ["get_goal_context", "search_knowledge", "memory_search"],
        "memory": ["memory_search", "get_goal_context"],
        "dashboard": ["get_dashboard"],
    }[route]
    if route == "strategy":
        tools.insert(0, {"name": "get_goal_context", "arguments": {}})
    elif route == "memory":
        tools = [{"name": "memory_search", "arguments": {}}]
    return Plan(
        intent=route,
        tools=tools,
        reason=reason,
        routing=routing or {},
        allowed_tools=allowed,
        max_tool_calls=2 if route in {"strategy", "knowledge", "memory"} else 1,
    )
