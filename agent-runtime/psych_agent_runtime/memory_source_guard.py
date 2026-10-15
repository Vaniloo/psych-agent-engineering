"""Small first-person boundary; ambiguous or mixed records remain model decisions."""

import re

_ZH_OTHER = (
    r"(?:朋友|同事|同学|室友|姐姐|妹妹|哥哥|弟弟|导师|老师|爸爸|妈妈|父亲|母亲|伴侣|丈夫|妻子)"
)
_EN_OTHER = r"(?:friend|colleague|classmate|roommate|sister|brother|tutor|teacher|father|mother|partner|husband|wife)"
_OTHER = re.compile(rf"{_ZH_OTHER}|\b{_EN_OTHER}s?\b", re.IGNORECASE)
_SELF_QUERY = re.compile(r"我|\b(?:my|me|I)\b", re.IGNORECASE)
_OTHER_SUBJECT = re.compile(
    rf"^(?:我(?:的)?\s*)?{_ZH_OTHER}|^(?:my\s+)?{_EN_OTHER}\b", re.IGNORECASE
)
_OWN_CLAUSE = re.compile(
    rf"^(?:但|而|不过|同时|但是|but\s+|and\s+|however\s*,?\s*)?"
    rf"(?:我(?!(?:的)?\s*{_ZH_OTHER})|I\b|my\s+(?!{_EN_OTHER}\b))",
    re.IGNORECASE,
)


def clearly_reverse_direction(query: str, record: dict) -> bool:
    """Reject only explicit incoming-only sources for an outgoing-action query.

    Mixed sources, negated own actions and unclear grammar remain model choices.
    This is not a general relation extractor or an event-completion classifier.
    """
    outgoing_query = re.search(
        r"我(?:曾经|之前|昨天|今天|最近)?给谁(?:发|寄|送)|"
        r"\bwho did I (?:call|email|message|send)\b",
        query,
        re.IGNORECASE,
    )
    if not outgoing_query:
        return False
    content = record["content"]
    own_action = re.search(
        r"我(?:昨天|今天|最近|之前)?(?:没(?:有)?|未)?给[^。；\n]{0,20}(?:发|寄|送)|"
        r"\bI\s+(?:(?:actually|never)\s+)?(?:called|emailed|messaged|sent|did not)\b",
        content,
        re.IGNORECASE,
    )
    own_action = own_action or re.search(
        r"(?:^|[，,。；;]|\band\s+)\s*(?:而|同时|然后|不过)?我[^。；\n]{0,24}(?:发|寄|送)|"
        r"\bI\b[^.!?\n]{0,30}\b(?:called|emailed|messaged|sent|call|email|message|send)\b",
        content,
        re.IGNORECASE,
    )
    if own_action:
        return False
    return bool(
        re.search(
            r"^[^。；\n]{1,24}给我(?:发|寄|送)|"
            r"^(?!I\b).{1,80}\b(?:called|emailed|messaged|sent)\s+me\b|"
            r"^I was (?:called|emailed|messaged) by\b",
            content,
            re.IGNORECASE,
        )
    )


def clearly_other_person(query: str, record: dict) -> bool:
    """Reject only clearly unrelated third-person subjects in a self-scoped query.

    Queries about relatives, joint events, quotations, and mixed self/other facts
    stay with the model. This is not an entity resolver or a relation-direction judge.
    """
    if not _SELF_QUERY.search(query) or _OTHER.search(query):
        return False
    if re.search(r"家人|家庭|\bfamily\b", query, re.IGNORECASE):
        return False
    content = record["content"].strip()
    if not _OTHER_SUBJECT.match(content):
        return False
    clauses = re.split(r"[，,。；;.!?]\s*", content)
    # An action addressed to the user is a fact about their experience as well.
    if re.search(
        r"(?:给|对|问|告诉|陪|和|跟|带)我|\b(?:me|us|with\s+my)\b", content, re.IGNORECASE
    ):
        return False
    return not any(_OWN_CLAUSE.match(clause.strip()) for clause in clauses)


def reviewable_personal_source(query: str, record: dict) -> bool:
    """Conservative sources for final-response review, not accepted model evidence."""
    return bool(
        _SELF_QUERY.search(query) and not _OTHER.search(query)
        and record.get("role") == "user" and not record.get("truncated")
        and _SELF_QUERY.search(record["content"]) and not _OTHER.search(record["content"])
    )
