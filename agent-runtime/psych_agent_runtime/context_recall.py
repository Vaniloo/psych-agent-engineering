"""Bounded cross-session context for turns with too little local context."""

import re

from .route_policy import explicit_knowledge_request, explicit_memory_request


def with_context_recall(plan, message, context):
    if plan.intent not in {"chat", "clarify", "knowledge", "strategy"}:
        return plan
    if "memory_search" in plan.required_tools or explicit_memory_request(message):
        return plan
    if plan.routing.get("deferredReads"):
        return plan
    if re.search(
        r"(?:不要|不用|无需|别|不想|不必)[^。！？\n]{0,20}(?:查|记忆|记录|历史|过去|之前|回顾)|"
        r"只(?:聊|考虑|根据)[^。！？\n]{0,8}(?:这次|本轮|现在)|重新开始|"
        r"\b(?:don't|do not)\s+(?:recall|use|search|look up)\b",
        message,
        re.IGNORECASE,
    ):
        return plan
    # Definitions and quoted examples do not need personal background.
    if explicit_knowledge_request(message) or re.search(
        r"什么是|解释|介绍|举例|假如|例如", message
    ):
        return plan
    meaningful = re.sub(r"[\s，。！？,.!?]", "", message)
    if meaningful.lower() in {"你好", "您好", "嗨", "谢谢", "谢谢你", "好的", "ok", "hello", "hi"}:
        return plan
    if len(meaningful) < 5 and meaningful not in {"继续", "还是这样", "又来了"}:
        return plan
    # Consult older context for a thin new conversation or an unresolved
    # continuation. Established local conversations otherwise keep their scope.
    users = [m for m in context.messages if m.role == "user" and m.content.strip()]
    continuation = bool(
        re.search(r"上次|之前那|以前那|那件事|还是这样|又来了|^继续[。！？\s]*$", message)
    )
    if len(users) > 2 and not continuation:
        return plan
    reason = "unresolved_continuation" if continuation else "thin_session"
    defaults = [t["name"] for t in plan.tools if t["name"] != "memory_search"]
    required = list(dict.fromkeys([*plan.required_tools, *defaults, "memory_search"]))
    if len(required) > 2:
        return plan
    plan.allowed_tools = list(dict.fromkeys([*plan.allowed_tools, "memory_search"]))
    plan.required_tools = required
    plan.max_tool_calls = max(plan.max_tool_calls, len(required))
    plan.routing = {**plan.routing, "contextRecall": {"mode": "cross_session", "reason": reason}}
    return plan


def background_query(message):
    # Search for the missing background, not the generic wording “today/tired/
    # reply briefly”, which otherwise ranks repeated check-ins above preferences.
    if re.search(r"累|疲惫|疲倦|放松|休息|歇一", message) and not re.search(
        r"上次|之前那|那件事", message
    ):
        return "我平时喜欢什么放松方式或活动？"
    text = re.sub(
        r"(?:请)?(?:用)?(?:一|两|三|几)?(?:句(?:话)?|句话)?(?:简短|简要)(?:回答|回复|回应|说说)[。！？\s]*$",
        "",
        message,
    ).strip()
    return text[:3900] or message[:3900]
