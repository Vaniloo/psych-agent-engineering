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
    r"(?P<question>怎么样|怎样|如何|是什么|怎么了|咋了|咋样|好不好|呢)?[呀啊呢嘛？?。！!\s]*$"
)
_FACT_QUESTION = re.compile(r"什么|啥|谁|哪|几点|几月|几日|几号|多久|几次|多少|怎么|咋|如何|怎样|吗")
_OWN_FACT = re.compile(
    r"我(?:平时|平常|通常|一般|习惯)|"
    r"我(?:以前|之前|曾经)?(?:更)?(?:喜欢|爱听|偏好)[^。！？\n]{0,12}(?:什么|啥|哪)|"
    r"我(?:家(?:里)?的|养的|的)[^。！？\n]{0,14}(?:叫|名字|生日|职业|住)|"
    r"(?:还记得我|(?:上次|之前|以前)我(?:说|提|聊)|我(?:上次|之前|以前)(?:说|提|聊))"
)
_ADVICE = re.compile(r"应该|该怎么|建议|适合|能不能|可以怎么|才能|比较好|要不要|假设|明天|后天|下周")
_RECENT_SELF = re.compile(
    r"(?:我(?:的)?(?:最近|近期|这几天|这两天|这周|今天|现在)|"
    r"(?:最近|近期|这几天|这两天|这周|今天|现在)我(?:的)?)"
)
_QUESTION_END = re.compile(
    r"(?:什么|啥|谁|哪(?:里|儿|个|些|家|种|类)|几点|几月|几日|几号|多久|几次|多少|"
    r"怎么样|怎样|如何|吗)(?:[\u3400-\u9fff]{0,6})?[呀啊呢来着？?。！!\s]*$"
)
_RECENT = re.compile(
    r"(?:看|查|回顾|总结|翻)[^。！？\n]{0,16}(?:最近|近期|刚才)[^。！？\n]{0,8}(?:对话|聊天|记录)|"
    r"(?:看|查|回顾|总结)[^。！？\n]{0,8}我最近(?:说了|聊了|提到)[^。！？\n]{0,8}(?:什么|哪些)"
)


def personal_recall_request(message, context=None):
    text = re.sub(r'“[^”]*”|「[^」]*」|‘[^’]*’|"[^"\n]*"', "", message).strip()
    if _EXCLUDED.search(message):
        return None
    # Broad recaps need the same read guarantee as narrow fact questions.
    if re.fullmatch(r"(?:那|所以)?你(?:现在|目前)?对我有?(?:什么|哪些|多少)了解(?:吗)?[呀啊呢？?。\s]*", text):
        return {"topic": "personal_overview", "timeWindow": None, "query": text}
    if re.search(r"我(?:在)?(?:其他|别的|之前|以前)的?(?:对话|会话|聊天)(?:里(?:面)?|中)?(?:说|聊|提)(?:了|过)?(?:什么|哪些)", text):
        return {"topic": "recent_conversations", "timeWindow": None, "query": text}
    text = re.sub(
        r"((?:我(?:的)?(?:今天|最近|这几天|这两天|这周|现在)|"
        r"(?:今天|最近|这几天|这两天|这周|现在)我(?:的)?))"
        r"(?:是)?(?:什么|啥)(?:样的)?(心情|情绪|状态)",
        r"\1\2怎么样", text,
    )
    # Compose independently requested reads instead of treating a whole turn
    # as one route. Keep the existing whole-turn quote/opt-out exclusion above;
    # this must never reopen a personal read the user declined. Recurse only on
    # proper clauses, without inheriting an unrelated history follow-up.
    # Keep enumeration punctuation inside one query: “喜欢听什么、在哪里
    # 休息” shares its subject and must retain both requested dimensions.
    clauses = [part.strip() for part in re.split(r"[，,。；;！？!?\n]", text) if part.strip()]
    if len(clauses) > 1:
        reads = [spec for part in clauses if (spec := personal_recall_request(part))]
        if reads:
            if len(reads) == 1:
                # A second question can share the first clause's subject.
                # Preserve it rather than silently dropping an unparsed facet.
                if any(_FACT_QUESTION.search(part) and not re.search(r"我|你|他|她", part)
                       and not personal_recall_request(part)
                       for part in clauses):
                    return {**reads[0], "query": text}
                return reads[0]
            return {
                "topic": "personal_fact",
                "timeWindow": reads[0]["timeWindow"]
                if all(spec["timeWindow"] == reads[0]["timeWindow"] for spec in reads) else None,
                "query": "；".join(dict.fromkeys(spec["query"] for spec in reads)),
            }
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
    # Questions about one's recent experience, habits or held facts are reads,
    # regardless of session length or topic. Advice stays chat; this grants no
    # write permission and leaves relevance/evidence checks to memory retrieval.
    mood_comparison = bool(
        re.search(r"(?:我今天|今天我)[^。！？\n]{0,10}(?:开心|高兴|低落|难过)", text)
        and re.search(r"(?:开心|高兴|低落|难过)[^。！？\n]{0,4}还是|吗|好不好", text)
    )
    # Require a question-shaped clause, not just an indefinite word inside an
    # assertion (e.g. “没什么胃口” / “做什么都没意思”). Do not turn mixed
    # statement+question turns into pure lookup evidence exclusions.
    recent_question = bool(
        _RECENT_SELF.search(text) and _QUESTION_END.search(text)
        and not re.search(r"[，,。；;！？!?\n].*\S|没什么|什么都|什么也|不知道", text)
    )
    if not _ADVICE.search(text) and (_FACT_QUESTION.search(text) or mood_comparison) and (
        _OWN_FACT.search(text) or mood_comparison or recent_question
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
