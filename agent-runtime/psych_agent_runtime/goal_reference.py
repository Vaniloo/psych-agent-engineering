"""Resolve a short status reply to one owned step in the adjacent exchange.

The prior assistant supplies a reference, never completion evidence. The current
user must report an outcome; the backend independently checks its saved receipts.
"""

import re

_STATUS = re.compile(
    r"(?:ok|好的|好|嗯)?(?:那|这一步|这个步骤)?(?:我)?"
    r"(?:今天|刚刚|刚才|已经)?(?:这一步|这个步骤)?(?:已经)?"
    r"(?P<status>完成了|做完了|搞定了|做了一点|做了一部分)"
    r"(?:啦|呢|哦)?", re.IGNORECASE,
)
_STEP_QUERY = re.compile(
    r"(?:那|请|你说说)?(?:第(?P<ordinal>[一二三四五六七八九十1-9])(?:个)?|当前|现在的|这)?"
    r"(?:小)?(?:步骤|行动)(?:是什么|是啥|怎么做|要做什么|呢)[？?。！!\s]*"
)
_NUMBER_TAG = re.compile(
    r"(?:第(?P<before>[一二三四五六七八九十\d]+)(?:个)?(?:小)?(?:步骤|步|行动)|"
    r"(?:小)?(?:步骤|行动)(?P<after>[一二三四五六七八九十\d]+))"
)


def status_report(message):
    if len(message) > 500 or re.search(r"[?？]", message):
        return None
    text = message.translate(str.maketrans("０１２３４５６７８９", "0123456789"))
    text = re.sub(r"\s", "", text)
    tags = list(_NUMBER_TAG.finditer(text))
    if len(tags) > 1:
        return None
    position = None
    if tags:
        tag = tags[0]
        value = tag["before"] or tag["after"]
        numbers = {s: i for i, s in enumerate(
            ["一", "二", "三", "四", "五", "六", "七", "八", "九", "十", "十一", "十二"], 1)}
        number = int(value) if value.isascii() else numbers.get(value, 0)
        if not 1 <= number <= 12:
            return None
        position = number - 1
        text = text[:tag.start()] + text[tag.end():]
    match = _STATUS.fullmatch(re.sub(r"[\s，,。.!！]", "", text))
    if not match:
        return None
    return {"position": position,
            "outcome": "partial" if match["status"] in {"做了一点", "做了一部分"} else "done"}


def followup_outcome(message):
    report = status_report(message)
    return report["outcome"] if report else None


def reference_text(text):
    return re.sub(r"[\s，,。.!！?？：:；;、]", "", text).replace("或者", "或")


def step_query(message):
    return _STEP_QUERY.fullmatch(message.strip()) is not None


def resolve_reference(message, goals, context, conversation_goal_id=None):
    report = status_report(message)
    if report is None:
        return None
    outcome, requested_position = report["outcome"], report["position"]
    if requested_position is not None and conversation_goal_id is not None:
        focused = [g for g in goals if g["id"] == conversation_goal_id and g["status"] == "active"]
        if len(focused) != 1 or requested_position >= len(focused[0]["actions"]):
            return None
        goal = focused[0]
        action = goal["actions"][requested_position]
        if action["status"] != "planned":
            return None
        return {"goalId": goal["id"], "version": goal["version"], "goalTitle": goal["title"],
                "actionId": action["id"], "actionTitle": action["title"],
                "outcome": outcome, "quote": message}
    if not context:
        return None
    messages = context.get("messages", []) if isinstance(context, dict) else context.messages
    rows = [m if isinstance(m, dict) else m.model_dump() for m in messages]
    if len(rows) < 2 or rows[-1]["role"] != "assistant" or rows[-2]["role"] != "user":
        return None
    query = _STEP_QUERY.fullmatch(rows[-2]["content"].strip())
    if not query:
        return None
    ordinal = query["ordinal"]
    position = None
    if ordinal:
        position = int(ordinal) - 1 if ordinal.isascii() else "一二三四五六七八九十".index(ordinal)
    if requested_position is not None:
        if position is not None and position != requested_position:
            return None
        position = requested_position
    reply = reference_text(rows[-1]["content"])
    # Include every owned active goal, not just the model's eight candidates.
    mentioned = [{"goalId": g["id"], "version": g["version"], "goalTitle": g["title"],
                  "actionId": a["id"], "actionTitle": a["title"], "position": index}
                 for g in goals if g["status"] == "active"
                 for index, a in enumerate(g["actions"]) if a["status"] == "planned"
                 and reference_text(a["title"]) in reply]
    # Repeated titles in different owned goals are ambiguous too.
    if len(mentioned) != 1:
        return None
    matched = mentioned[0]
    actual_position = matched.pop("position")
    if position is not None and position != actual_position:
        return None
    return {**matched, "outcome": outcome, "quote": message}
