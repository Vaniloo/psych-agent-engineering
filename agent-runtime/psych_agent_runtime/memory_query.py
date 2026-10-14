"""Bounded query facets, not generated facts or a second planning agent."""

import re
from dataclasses import dataclass

from .bm25 import BM25
from .personal_recall import personal_recall_request


@dataclass(frozen=True)
class QueryFacet:
    name: str
    query: str
    terms: str


def lookup_query(query: str) -> str:
    """Isolate a marked recall clause from a mixed read request, without generation."""
    parts = re.split(
        r"(?:，|,|；|;|。)?\s*(?:再|然后|另外|and then)\s*", query, flags=re.IGNORECASE
    )
    if len(parts) > 1:
        for part in reversed(parts):
            if re.search(
                r"按.*记录|根据.*记录|回顾|我之前|我以前|\b(?:recall|remember)\b",
                part,
                re.IGNORECASE,
            ):
                return part.strip()
    return query


def recall_only_question(content: str) -> bool:
    """Recognize only standalone recall questions without asserted answer facts."""
    text = content.strip()
    if personal_recall_request(text):
        return True
    if not re.match(
        r"(?:你(?:还)?记得我|我(?:以前|之前|曾经)(?:说过|提到|聊过)|"
        r"(?:请)?(?:根据|回顾)[^。！？\n]{0,12}(?:记录|我说过)|"
        r"(?:do you remember|what (?:did|have) I))",
        text,
        re.IGNORECASE,
    ):
        return False
    if not re.search(
        r"什么|哪里|哪些|哪类|哪家|哪种|哪个|几点|多久|\b(?:what|where|how|when)\b",
        text,
        re.IGNORECASE,
    ):
        return False
    if re.search(
        r"[，,。；;]\s*我(?:喜欢|不喜欢|想|昨天|今天|每|通常)|"
        r"\bI (?:like|prefer|love|went|did)\b",
        text,
        re.IGNORECASE,
    ):
        return False
    # A quoted fact or an additional declarative sentence can contain evidence.
    return not re.search(r'[“”「」"\n]|[。.!?？]\s*\S', text)


def query_facets(query: str) -> list[QueryFacet]:
    # Split only a genuinely multi-field question. A single question keeps the
    # original trained contract, including negation and relationship direction.
    music = re.search(r"听什么|音乐|音乐偏好|\b(?:music|listen)\b", query, re.IGNORECASE)
    place = re.search(r"在哪里|哪(?:里|个地方)|地点|场所|\bwhere\b", query, re.IGNORECASE)
    rest = re.search(r"休息|放松|\b(?:rest|resting|relax|relaxing)\b", query, re.IGNORECASE)
    if re.search(
        r"不喜欢|不爱|讨厌|\b(?:dislike|hate|don't like|do not like)\b", query, re.IGNORECASE
    ):
        return []
    # Preserve third-person and relationship scope in every rewrite path.
    if re.search(
        r"朋友|同事|家人|姐姐|妹妹|哥哥|弟弟|父母|妈妈|爸爸|伴侣|"
        r"\b(?:friend|colleague|family|sister|brother|partner|parent)\b",
        query,
        re.IGNORECASE,
    ):
        return []
    if not (music and place and rest):
        # Two explicit questions can be scored separately within the existing
        # batch. Shared first-person context is retained, without asking another
        # model to generate queries or invent answers.
        parts = re.split(r"[，,、；;]|以及|\band\b", query, flags=re.IGNORECASE)
        question = r"什么|谁|哪里|哪(?:个|家|种|类)|几次|多久|多长|多少|吗|是否|有没有|\b(?:what|who|where|how|did|does)\b"
        if len(parts) != 2 or not all(re.search(question, part, re.IGNORECASE) for part in parts):
            return []
        if not re.search(r"我|\b(?:my|I)\b", parts[0], re.IGNORECASE):
            return []
        preference = re.match(
            r"我(?:以前|之前|现在)?(?:更)?(?:喜欢|偏好|通常|习惯)", parts[0].strip()
        )
        second = parts[1].strip()
        shared_action = bool(
            preference
            and len(parts[0]) > preference.end()
            and parts[0][preference.end()] in second
            and re.match(r"在|于|到", second)
        )
        second_query = (preference.group() + second) if shared_action else "我的这项经历：" + second
        return [
            QueryFacet("part_1", parts[0].strip(), parts[0].strip()),
            QueryFacet("part_2", second_query, second),
        ]
    english = not re.search(r"[\u3400-\u9fff]", query)
    return [
        QueryFacet(
            "music",
            "What music do I like listening to?" if english else "我喜欢听什么音乐？",
            "音乐 听 歌 music listen",
        ),
        QueryFacet(
            "rest_place",
            "Where do I prefer resting?" if english else "我喜欢在哪里休息？",
            "休息 地方 地点 场所 resting place",
        ),
    ]


def facet_for_record(facets: list[QueryFacet], content: str) -> QueryFacet | None:
    if not facets:
        return None
    if facets[0].name == "part_1":
        matches = BM25([f.terms for f in facets]).search(content, 1)
        return facets[matches[0].index] if matches else None
    # A record about music can also say "rest". Prefer its concrete topic.
    if re.search(r"音乐|听|歌|\b(?:music|listen|piano|jazz|rock)\b", content, re.IGNORECASE):
        return facets[0]
    if re.search(
        r"休息|地方|地点|场所|\b(?:rest|resting|balcony|garden|place)\b", content, re.IGNORECASE
    ):
        return facets[1]
    return None
