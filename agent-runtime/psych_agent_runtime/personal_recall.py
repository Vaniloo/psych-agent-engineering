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
    r"(?P<question>怎么样|怎样|如何|是什么|咋样|好不好|呢)?[呀啊呢嘛？?。！!\s]*$"
)
_FACT_QUESTION = re.compile(r"什么|啥|谁|哪|几点|几月|几日|几号|多久|几次|多少|怎么|咋|如何|怎样|吗")
_OWN_FACT = re.compile(
    r"我(?:平时|平常|通常|一般|习惯)|"
    r"我(?:更)?(?:喜欢|爱听|偏好)[^。！？\n]{0,12}(?:什么|啥|哪)|"
    r"我(?:家(?:里)?的|养的|的)[^。！？\n]{0,14}(?:叫|名字|生日|职业|住)|"
    r"(?:还记得我|(?:上次|之前|以前)我(?:说|提|聊)|我(?:上次|之前|以前)(?:说|提|聊))"
)
_ADVICE = re.compile(r"应该|该怎么|建议|适合|能不能|可以怎么|才能|比较好|要不要|假设|明天|后天|下周")
_RECENT = re.compile(
    r"(?:看|查|回顾|总结|翻)[^。！？\n]{0,16}(?:最近|近期|刚才)[^。！？\n]{0,8}(?:对话|聊天|记录)|"
    r"(?:看|查|回顾|总结)[^。！？\n]{0,8}我最近(?:说了|聊了|提到)[^。！？\n]{0,8}(?:什么|哪些)"
)


def personal_recall_request(message, context=None):
    text = re.sub(r'“[^”]*”|「[^」]*」|‘[^’]*’|"[^"\n]*"', "", message).strip()
    if _EXCLUDED.search(message):
        return None
    text = re.sub(
        r"((?:我(?:的)?(?:今天|最近|这几天|这两天|这周|现在)|"
        r"(?:今天|最近|这几天|这两天|这周|现在)我(?:的)?))"
        r"(?:是)?(?:什么|啥)(?:样的)?(心情|情绪|状态)",
        r"\1\2怎么样", text,
    )
    # A bare temporal subject alone is not enough.
    if (match := _STATE.search(text)) and (match.group("topic") or match.group("question")):
        period = match.group("a") or match.group("b")
        mood = match.group("topic") in {"心情", "情绪"}
        return {
            "topic": "mood" if mood else "personal_state",
            "timeWindow": "today" if period == "今天" else None,
            "query": "我今天心情怎么样？"
            if period == "今天" and mood
            else "我今天的状态怎么样？"
            if period == "今天"
            else "我最近心情怎么样？" if mood else "我最近的状态怎么样？",
        }
    # A question about an already held preference or named possession is a
    # factual read, even without the literal phrase “你记得我”. Advice stays chat.
    mood_comparison = bool(
        re.search(r"(?:我今天|今天我)[^。！？\n]{0,10}(?:开心|高兴|低落|难过)", text)
        and re.search(r"(?:开心|高兴|低落|难过)[^。！？\n]{0,4}还是|吗|好不好", text)
    )
    if not _ADVICE.search(text) and (_FACT_QUESTION.search(text) or mood_comparison) and (
        _OWN_FACT.search(text) or mood_comparison
    ):
        query = text.replace("啥", "什么").replace("来着", "")
        if re.search(r"我(?:平时|平常|通常|一般)[^。！？\n]{0,10}放松", text):
            query = "我平时喜欢什么放松方式或活动？"
        return {
            "topic": "mood" if mood_comparison else "personal_fact",
            "timeWindow": "today" if mood_comparison else None,
            "query": "我今天心情怎么样？" if mood_comparison else query,
        }
    short = bool(re.fullmatch(r"(?:那|然后|所以)?(?:怎么样|怎样|如何|呢)[？?。！!\s]*", text))
    recent = bool(_RECENT.search(text))
    source_followup = bool(re.fullmatch(
        r"(?:那|所以)?(?:你(?:是)?怎么知道(?:的)?|你从哪(?:里|儿)知道(?:的)?|"
        r"这(?:个)?(?:信息|说法)?(?:的)?(?:依据|来源)是什么)[呀啊呢？?。！!\s]*", text,
    ))
    if not (short or recent or source_followup):
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
            return {**spec, "followUp": True, **({"sourceFollowUp": True} if source_followup else {})}
        if source_followup:
            # Provenance refers only to the immediately preceding user topic;
            # do not resurrect an older fact query after an unrelated exchange.
            break
    return (
        {
            "topic": "recent_conversations",
            "timeWindow": None,
            "query": "我最近在其他对话中表达过哪些经历、状态或感受？",
        }
        if recent
        else None
    )
