"""Extractive candidates and narrow attribute boundaries, never semantic write authority."""

import re
import unicodedata

_SELF = re.compile(r"我|自己|\b(?:I|my|me)\b", re.IGNORECASE)
_CORRECTION = re.compile(r"更正|纠正|记错|说错|误写|不是.+而是|\bcorrection\b", re.IGNORECASE)
_PREFIX = re.compile(r"^(?:(?:更正|纠正|补充|请记住|帮我记住)(?:一下)?[：:，,\s]*)+")
_MUSIC = re.compile(
    r"音乐|民谣|爵士|钢琴曲|古琴|摇滚|白噪音|古典|吉他曲|笛子曲|轻音|小提琴|口琴|合唱"
)
_PET = re.compile(
    r"^我(?:的|家(?:里)?的|养的|唯一(?:的|一只)?)(兔子|猫|狗|乌龟|鹦鹉)(?:的)?(?:名字(?:是)?|名叫|叫)"
)


def fact_spans(text):
    """Only split independent explicit self clauses. Corrections stay together."""
    spans = []
    for sentence in re.finditer(r".+?(?:[。！？\n]|[.!?](?=\s|$)|$)", text, re.DOTALL):
        content = sentence.group()
        cuts = (
            []
            if _CORRECTION.search(content)
            else list(re.finditer(r"[，,；;]\s*(?=我|I\b|My\b)", content, re.IGNORECASE))
        )
        start = sentence.start()
        for end, following in [
            (sentence.start() + c.start(), sentence.start() + c.end()) for c in cuts
        ] + [(sentence.end(), sentence.end())]:
            raw = text[start:end]
            left = start + len(raw) - len(raw.lstrip())
            right = end - len(raw) + len(raw.rstrip())
            value = text[left:right]
            if value and len(value) <= 400 and _SELF.search(value):
                scope = (
                    "temporary"
                    if re.search(
                        r"今天|昨天|今晚|刚才|此刻|这次|最近|这几天|\b(?:today|yesterday|tonight)\b",
                        value,
                        re.IGNORECASE,
                    )
                    else "stable"
                )
                spans.append((left, right, scope))
            start = following
    return spans


def attribute_key(content):
    """Recognize only explicit single-attribute self statements; unknown stays unknown.

    Equality is not evidence of duplication or replacement. The small recognizers
    only veto cross-attribute targets and restrict experimental destructive writes.
    """
    text = unicodedata.normalize("NFKC", content).strip()
    text = _PREFIX.sub("", text)
    if text.count("我") != 1 or re.search(r"[。！？].+\S", text):
        return None
    music_predicate = bool(
        re.search(r"(?:喜欢|爱|讨厌|偏爱).{0,4}听", text) and _MUSIC.search(text)
    )
    if (
        sum(
            (
                music_predicate,
                bool(re.search(r"(?:兔子|猫|狗|乌龟|鹦鹉)(?:的)?(?:名字|名叫|叫)", text)),
                bool(re.search(r"(?:职业|工作)(?:现在)?是", text)),
            )
        )
        > 1
    ):
        return None
    keys = []
    pet = _PET.match(text)
    if pet:
        keys.append(("self.pet." + pet.group(1), "name"))
    if (
        re.match(
            r"^我(?:平时|一直|通常|一向|现在|最|也|更|不再|仍然|长期|很|特别|不|喜欢|爱|讨厌|偏爱|最喜欢)",
            text,
        )
        and music_predicate
    ):
        keys.append(("self", "preference.music"))
    if re.match(r"^我(?:的)?(?:职业|工作)(?:现在)?是", text):
        keys.append(("self", "occupation"))
    return keys[0] if len(keys) == 1 else None


def attribute_relation(left, right):
    a, b = attribute_key(left), attribute_key(right)
    return "unknown" if a is None or b is None else "same" if a == b else "different"


def target_verified(action, content, existing):
    relation = attribute_relation(content, existing)
    if action == "replace":
        return relation == "same"
    if action == "support":
        return relation == "same" or content.strip() == existing.strip()
    return True
