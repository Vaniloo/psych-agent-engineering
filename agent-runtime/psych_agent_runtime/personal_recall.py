"""Recognize factual personal recaps without requiring the word 'remember'."""

import re

_EXCLUDED = re.compile(
    r"举例|例如|假如|引用|解释这句话|他说|她说|朋友的|别人的|"
    r"(?:不要|不用|无需|不必|别|不想)[^。！？\n]{0,20}(?:查|回顾|记忆|记录|对话)|"
    r"只(?:聊|根据|考虑)[^。！？\n]{0,8}(?:这次|本轮|现在)|重新开始"
)
_STATE = re.compile(
    r"(?:我(?:的)?(?P<a>今天|最近|这几天|这两天|这周|现在)(?:的)?|"
    r"(?P<b>今天|最近|这几天|这两天|这周|现在)我(?:的)?)"
    r"(?P<topic>心情|情绪|状态|近况|情况|过得|过的)?"
    r"(?P<question>怎么样|怎样|如何|是什么|呢)?[？?。！!\s]*$"
)
_RECENT = re.compile(
    r"(?:看|查|回顾|总结|翻)[^。！？\n]{0,16}(?:最近|近期|刚才)[^。！？\n]{0,8}(?:对话|聊天|记录)|"
    r"(?:看|查|回顾|总结)[^。！？\n]{0,8}我最近(?:说了|聊了|提到)[^。！？\n]{0,8}(?:什么|哪些)"
)


def personal_recall_request(message, context=None):
    text = re.sub(r'“[^”]*”|「[^」]*」|‘[^’]*’|"[^"\n]*"', "", message).strip()
    if _EXCLUDED.search(message):
        return None
    # A bare temporal subject alone is not enough.
    if (match := _STATE.search(text)) and (match.group("topic") or match.group("question")):
        period = match.group("a") or match.group("b")
        mood = match.group("topic") in {"心情", "情绪"}
        return {
            "topic": "mood" if mood else "personal_state",
            "timeWindow": "today" if period == "今天" else None,
            "query": "我今天表达过什么心情或感受？"
            if period == "今天" and mood
            else "我今天说过哪些经历、状态或感受？"
            if period == "今天"
            else "我近期表达过哪些状态或感受？",
        }
    short = bool(re.fullmatch(r"(?:那|然后|所以)?(?:怎么样|怎样|如何|呢)[？?。！!\s]*", text))
    recent = bool(_RECENT.search(text))
    if not (short or recent):
        return None
    messages = (
        context.get("messages", [])
        if isinstance(context, dict)
        else getattr(context, "messages", [])
    )
    users = [m for m in messages if (m.get("role") if isinstance(m, dict) else m.role) == "user"]
    for item in reversed(users[-6:]):
        content = item.get("content", "") if isinstance(item, dict) else item.content
        if re.search(r"重新开始|换个话题", content):
            break
        if content == message:
            continue
        if (spec := personal_recall_request(content)) and spec["topic"] != "recent_conversations":
            return {**spec, "followUp": True}
    return (
        {
            "topic": "recent_conversations",
            "timeWindow": None,
            "query": "我最近在其他对话中表达过哪些经历、状态或感受？",
        }
        if recent
        else None
    )
