"""Bounded knowledge grounding for current distress, independent of risk labels."""

import re

from .goal_creation import explicit_creation
from .route_policy import explicit_knowledge_request, explicit_memory_request
from .safety import SafetyScanner

TOPICS = (
    (r"社交焦虑|害怕被评价", "社交焦虑"),
    (r"焦虑|特别紧张|很紧张|紧张得|脑子.{0,4}停不下来|(?:一直|总是|老是?|反复).{0,4}担心", "焦虑"),
    (r"压力(?:很|太|特别)?大|压力山大|压力很重", "压力"),
    (r"失眠|睡不着|入睡困难", "睡眠"),
    (r"情绪低落|心情低落|特别低落|很低落|抑郁", "情绪低落"),
    (r"惊恐发作|惊恐", "惊恐"),
)
LISTEN = re.compile(
    r"只想(?:倾诉|聊聊|说说|有人听)|听我说|只(?:要|需要).{0,8}(?:听|陪)|"
    r"(?:不要|不用|不想|(?<!特)别|先别).{0,12}(?:建议|方法|科普|知识|分析|解释)|"
    r"只想安静|不想说话|暂时不想聊"
)
ASK_METHOD = re.compile(r"怎么办|怎么缓解|怎样缓解|给我.{0,8}(?:建议|方法)|带我练习|想试试")
NO_KNOWLEDGE = re.compile(
    r"(?:不要|不用|(?<!特)别|无需|先别).{0,12}(?:科普|讲知识|解释|分析)|"
    r"(?:不要|不用|(?<!特)别|无需|先别).{0,12}(?:查询|查找|检索|查).{0,8}(?:知识|资料|数据库)"
)


def with_support_knowledge(plan, message, support_level, context=None):
    if support_level == "crisis":
        return plan
    scoped = SafetyScanner.scoped_text(message)
    scoped = "，".join(
        clause for clause in re.split(r"[，。！？,;；\n]", scoped)
        if not re.search(
            r"^\s*(?:(?:我(?:的)?)?(?:朋友|同事|同学|家人|伴侣|妈妈|爸爸|父母|室友)|他说|她说)",
            clause,
        )
    )
    no_knowledge = bool(NO_KNOWLEDGE.search(scoped))
    if explicit_knowledge_request(message) and not no_knowledge:
        # Asking for a definition while declining advice still requests sources.
        return plan
    opted_out = no_knowledge or bool(LISTEN.search(scoped))
    if not opted_out and not ASK_METHOD.search(scoped) and not explicit_knowledge_request(message):
        # An earlier listening preference remains until the user requests help
        # or switches topics; assistant suggestions cannot change that preference.
        for item in reversed((context or {}).get("messages", [])[-6:]):
            if item.get("role") != "user":
                continue
            text = item.get("content", "")
            if re.search(r"换个话题|重新开始", text) or ASK_METHOD.search(text):
                break
            if LISTEN.search(text):
                opted_out = True
                break
    if opted_out:
        plan.allowed_tools = [n for n in plan.allowed_tools if n != "search_knowledge"]
        plan.required_tools = [n for n in plan.required_tools if n != "search_knowledge"]
        plan.tools = [t for t in plan.tools if t.get("name") != "search_knowledge"]
        if plan.intent == "knowledge":
            plan.intent = "chat"
        plan.routing["supportKnowledge"] = {"mode": "listening_only"}
        return plan
    if (explicit_creation(message) or explicit_memory_request(message)
            or plan.intent == "dashboard" or plan.routing.get("goalFocus")
            or re.search(r"举例|例如|假如|引用|什么是|解释|介绍|以前|过去|上次|之前", scoped)):
        return plan
    if not re.search(r"我|最近|现在|今天|这几天|感觉|一直|好焦虑|好紧张", scoped):
        return plan
    topic = next((name for pattern, name in TOPICS if re.search(pattern, scoped)), None)
    if not topic:
        return plan
    # A denial must not be converted into a current symptom by keyword matching.
    if re.search(r"(?:没有|不再|并不|不是|没觉得|不觉得|不感到|不怎么).{0,8}(?:焦虑|紧张|压力|失眠|低落|抑郁|惊恐|担心|停不下来)", scoped):
        return plan
    # Current distress needs general support, not a subtype's symptom list.
    # Explicit educational questions keep their original query above.
    query = f"面对{topic}困扰，有哪些自我照顾和放松方法？"
    if not ASK_METHOD.search(scoped):
        # A current feeling is not a factual memory request or a lesson, even
        # if a model selected one of those workflows. Retain permitted reads.
        plan.intent = "chat"
        plan.routing["effectiveRoute"] = "chat"
    plan.allowed_tools = list(dict.fromkeys([*plan.allowed_tools, "search_knowledge"]))
    required = list(dict.fromkeys([*plan.required_tools, "search_knowledge"]))
    if len(required) > 2:
        plan.routing["deferredReads"] = list(dict.fromkeys([
            *plan.routing.get("deferredReads", []), "search_knowledge"
        ]))
        mode = "deferred"
    else:
        plan.required_tools = required
        plan.max_tool_calls = max(plan.max_tool_calls, len(required))
        mode = "required"
    plan.routing["supportKnowledge"] = {"mode": mode, "topic": topic, "query": query}
    return plan
