"""Application-owned capability policies; model output never becomes tool arguments."""

from __future__ import annotations

import re

from .goal_context import goal_focus, goal_list_request
from .personal_recall import personal_recall_request
from .schemas import Plan

RESPONSE_POLICIES = {
    "chat": "本轮自然接着用户的话聊，回应具体细节；闲聊不必分析感受，个人困扰可以温和展开。用户未要求建议时不堆砌行动清单。",
    "knowledge": "解释知识和适用边界，优先使用检索证据；引用不足时明确说明。不要把情绪困扰归咎于用户的想法，现实处境也会影响感受和行为。",
    "memory": "仅依据已加载的真实记忆回答，逐项回答用户询问的事实，不泄露内部字段。",
    "dashboard": "按reportAvailability区分近期对话概览与正式报告，先说明有没有内容可看，再简短回顾；不推测趋势。",
    "strategy": "结合用户处境和知识证据，给出一到三个具体且容易开始的步骤。",
    "clarify": "先回应用户，再提出一个简短的澄清问题；不要猜测用户的经历或目标。",
}


def explicit_memory_request(message: str) -> bool:
    """A narrow read-only intent guarantee, independent of model confidence.

    Quoted examples and explicit opt-outs never authorize a personal lookup.
    This supplements capabilities, not risk decisions or write permissions.
    """
    if personal_recall_request(message):
        return True
    text = re.sub(r'“[^”]*”|「[^」]*」|‘[^’]*’|"[^"\n]*"', "", message)
    if re.search(
        r"(?:不要|不用|无需|不必|别|不想)(?:再)?[^。！？\n]{0,12}(?:查|回顾|记录|记忆)|"
        r"\b(?:don't|do not)\s+(?:look up|search|recall|use)\b",
        text,
        re.IGNORECASE,
    ):
        return False
    if not re.search(
        r"[?？]|什么|哪里|哪些|哪类|几点|多久|记得|回顾|查|根据|按.*记录|告诉|"
        r"\b(?:remember|recall|what|where|when|how|look up)\b",
        text,
        re.IGNORECASE,
    ):
        return False
    return bool(
        re.search(
            r"(?:你(?:还)?记得我|我(?:以前|之前|曾经)(?:有)?(?:说过|提到|聊过)|"
            r"(?:回顾|查(?:一下)?|根据)[^。！？\n]{0,20}(?:我说过|以前的记录|之前的记录|我的偏好)|"
            r"我说过的[^。！？\n]{0,20}(?:偏好|习惯|地点|爱好)|"
            r"(?:按|根据|从)[^。！？\n]{0,12}(?:已有|以前|之前|过往|历史)的?记录[^。！？\n]{0,24}(?:我|偏好|习惯))|"
            r"\b(?:do you remember what I|what (?:did I|have I) (?:say|tell)|"
            r"(?:recall|look up) my (?:previous|past|preferences))\b",
            text,
            re.IGNORECASE,
        )
    )


def explicit_knowledge_request(message: str) -> bool:
    text = re.sub(r'“[^”]*”|「[^」]*」|‘[^’]*’|"[^"\n]*"', "", message)
    concepts = (
        r"CBT|认知行为|正念|心理知识|心理学|心理疗法|心理治疗|行为激活|"
        r"焦虑|抑郁|强迫症|PTSD|ADHD|双相|惊恐|精神分裂|创伤|进食障碍|应激|压力"
    )
    return bool(
        re.search(
            rf"(?:什么是|解释|介绍)[^。！？\n]{{0,32}}(?:{concepts})|"
            rf"(?:{concepts})[^。！？\n]{{0,8}}(?:是什么|怎么理解)",
            text,
            re.IGNORECASE,
        )
    )


def guarantee_requested_reads(plan: Plan, message: str, context=None) -> Plan:
    """An explicit owned-memory read cannot be skipped by route/finish choices."""
    scoped = re.sub(r'“[^”]*”|「[^」]*」|‘[^’]*’|"[^"\n]*"', "", message)
    report = bool(
        re.search(
            r"(?:查看|看看|看下|看一下|查询|有没有|有无|是否有)[^。！？\n]{0,16}"
            r"(?:报告|概览|情绪趋势)|我的(?:心理)?报告[^。！？\n]{0,12}(?:有|看)",
            scoped,
        )
    ) and not re.search(r"(?:不要|不用|无需|别)[^。！？\n]{0,12}(?:报告|概览)", scoped)
    recap = personal_recall_request(message, context)
    focus = None if recap else goal_focus(message, context)
    goal = (
        bool(focus)
        or goal_list_request(scoped)
        and not re.search(r"(?:不要|不用|无需|别)[^。！？\n]{0,12}(?:查|看).*目标", scoped)
    )
    opted_out = bool(
        re.search(
            r"(?:不要|不用|无需|不必|别|不想)(?:再)?[^。！？\n]{0,12}(?:查|回顾|记录|记忆)|"
            r"\b(?:don't|do not)\s+(?:look up|search|recall|use)\b",
            scoped,
            re.IGNORECASE,
        )
    )
    quoted_request = (
        scoped != message
        and bool(re.search(r"记得我|我之前提到|我以前说过", message))
        and not explicit_memory_request(scoped)
    )
    if opted_out or quoted_request:
        if plan.intent == "memory":
            route = (
                "knowledge" if re.search(r"解释|什么是|explain", scoped, re.IGNORECASE) else "chat"
            )
            plan = plan_for_route(route, message, plan.reason, plan.routing)
        plan.allowed_tools = [t for t in plan.allowed_tools if t != "memory_search"]
        plan.required_tools = [t for t in plan.required_tools if t != "memory_search"]
        plan.tools = [t for t in plan.tools if t.get("name") != "memory_search"]
        plan.routing = {
            **plan.routing,
            "capabilityGuard": "personal_memory_read_excluded",
            "effectiveRoute": plan.intent,
        }
    memory = (explicit_memory_request(message) or bool(recap)) and not (opted_out or quoted_request)
    knowledge = explicit_knowledge_request(message)
    if not (memory or report or goal or knowledge):
        return plan
    intent = (
        "dashboard" if report else "strategy" if goal else "knowledge" if knowledge else "memory"
    )
    plan = plan_for_route(intent, message, plan.reason, plan.routing)
    requested = (
        (["get_dashboard"] if report else [])
        + (["get_goal_context"] if goal else [])
        + (["memory_search"] if memory else [])
        + (["search_knowledge"] if knowledge else [])
    )
    # Explicit reads supplement model permissions; writes and crisis handling
    # remain in their own guards. Three requested reads are visibly deferred.
    plan.allowed_tools = list(dict.fromkeys([*plan.allowed_tools, *requested[:2]]))
    plan.required_tools = requested[:2]
    plan.max_tool_calls = max(plan.max_tool_calls, len(plan.required_tools))
    plan.routing = {
        **plan.routing,
        "capabilityGuard": "explicit_owned_read",
        "effectiveRoute": plan.intent,
        "deferredReads": requested[2:],
    }
    if recap and memory:
        plan.routing["personalRecall"] = recap
    if focus:
        plan.routing["goalFocus"] = focus
    elif (goal and re.search(r"完成|做完|达成|结束", message)
          and re.search(r"哪些|什么|列表|清单|完成的目标|已完成目标|已达成的目标", message)
          and not re.search(r"没|未|还没", message)):
        plan.routing["goalStatusFilter"] = "completed"
    if opted_out or quoted_request:
        plan.allowed_tools = [t for t in plan.allowed_tools if t != "memory_search"]
    if (
        knowledge
        and not memory
        and not re.search(r"我|我的|结合|针对|\b(?:my|me)\b", scoped, re.IGNORECASE)
    ):
        plan.allowed_tools = [t for t in plan.allowed_tools if t != "memory_search"]
    return plan


def explicit_support_resource_request(message: str) -> bool:
    """Honor an explicit resource lookup; this does not change the risk estimate."""
    for clause in re.split(r"[。！？!?\n，,；;]", message):
        if re.search(r"不(?:想|需要|要|希望)|不用|无需|引用|举例|他说|她说", clause):
            continue
        if not re.search(r"心理|咨询|求助|援助热线|专业支持", clause):
            continue
        if re.search(
            r"(?:我(?:想|需要|希望)(?:寻找|找|获取|了解)?|(?:请)?(?:给我|帮我找|提供))"
            r"[^。！？\n]{0,40}(?:求助渠道|支持资源|咨询资源|咨询渠道|求助资源|援助资源|援助热线)",
            clause,
        ):
            return True
    return False


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
        required_tools=["memory_search"]
        if route == "memory"
        else ["get_dashboard"]
        if route == "dashboard"
        else [],
        max_tool_calls=2 if route in {"strategy", "knowledge", "memory"} else 1,
    )
