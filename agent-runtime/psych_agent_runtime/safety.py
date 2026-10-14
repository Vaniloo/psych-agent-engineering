from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class RiskScan:
    high_risk: bool
    score: float
    matched_terms: tuple[str, ...]
    action: str
    attributed_only: bool = False
    clear_denial: bool = False
    non_imminent_impulse: bool = False


class SafetyScanner:
    """Bounded source-aware guard. Concrete actions take priority over denials.

    This is a support-flow policy, not a clinical risk assessment. Explicit
    attribution is only a scope signal; it never suppresses an own action outside
    the attributed passage. The backend mirrors these rules with parity fixtures.
    """

    HIGH_RISK_PHRASES = (
        "想死",
        "不想活",
        "自杀",
        "轻生",
        "结束自己",
        "结束生命",
        "活着没意义",
        "伤害自己",
        "割腕",
        "跳楼",
        "服药自杀",
        "准备去死",
        "马上去死",
        "kill myself",
        "hurt myself",
        "end my life",
    )
    PAST_OTHER = r"(?:朋友|同事|室友)[^，。！？,.!?；;\n]{0,30}(?:说过|提过|曾说|曾提)"
    ATTRIBUTION = re.compile(
        r"小说|虚构|剧情|影视|电影|角色|主角|新闻|报道|翻译|(?:引用|转述|复述).{0,8}(?:朋友|同事|他人)|(?:朋友|同事|他|她)(?:说|的原话)|"
        + PAST_OTHER
    )
    NONSELF = re.compile(
        r"^(?:看到(?:影视|电影)?|新闻|报道|小说人物|小说主角|小说角色|影视人物|电影人物|影视角色|电影角色|角色|主角|文章|朋友说|同事说|他说|她说|我在(?:写小说|翻译)|"
        + PAST_OTHER
        + r")"
    )
    QUOTED = re.compile(r'“[^”]*”|「[^」]*」|‘[^’]*’|"[^"\n]*"')
    OWN_ACTION = re.compile(
        r"我(?:也|自己|现在|今晚|此刻|马上|已经|刚刚|刚|真的|同样)*(?:想|要|准备|打算|决定|正在)?(?:马上|现在)?(?:不想活|想死|死|自杀|轻生|伤害自己|结束生命|割腕|跳楼|跳下去|吞|服|吃|喝|写好|准备好)"
    )
    QUOTE_SOURCE = re.compile(
        r"(?:小说|影视|电影|角色|主角|新闻|报道|翻译|朋友|同事|他人|他|她)"
        r"[^，。！？,.!?；;\n“”「」‘’\"]{0,24}"
        r"(?:说|写|原话|台词|句子|引述|提到|内容|的话|的经历|翻译(?:剧本)?)"
        r"[^，。！？,.!?；;\n“”「」‘’\"]{0,8}$|翻译(?:剧本|句子|文本)?[:：]?\s*$"
    )
    OWN_REPORT = re.compile(
        r"我(?:自己|本人|现在|刚才)?(?:的?(?:想法|情况|经历|原话)|说|告诉你)|"
        r"这(?:是|就是)我(?:的|现在)|自己.{0,6}原话"
    )
    DENIAL = re.compile(
        r"(?:从来没有|并没有|不再有|没有|不会|并不|不是|不想|从未|没)"
        r"(?:想要|要|想过|想|任何|有)?(?:想死|死|自杀|轻生|自伤|伤害自己|结束生命|结束自己)"
        r"的?(?:想法|念头|意图|意思|打算|计划)?|"
        r"(?:do not|don't|won't|never)\s+(?:want to\s+)?(?:kill|hurt) myself"
    )
    DANGER = re.compile(
        r"(?:已经|刚刚|刚才|刚|正在|现在|已)[^，。！？,.!?；;\n]{0,14}?"
        r"(?P<dose_action>吞|服|吃|喝)[^，。！？,.!?；;\n]{0,6}?"
        r"(?:大量|很多|过量|一大把|整瓶)[^，。！？,.!?；;\n]{0,5}(?:药|药片)|"
        r"(?:楼顶|天台|屋顶|桥边|桥上|阳台|窗台|高层|高楼)"
        r"[^。！？.!?；;\n]{0,30}?(?P<height_action>跳下去|跳下|往下跳|翻过去)|"
        r"(?:已经|刚|正在)[^，。！？,.!?；;\n]{0,8}?"
        r"(?P<letter_action>写好|准备好|留好)[^，。！？,.!?；;\n]{0,3}(?:遗书|告别信)"
    )
    SEVERE = re.compile(
        r"绝望|撑不住|无法.{0,8}(?:生活|工作|上课|照顾)|没法.{0,8}(?:生活|工作|上班|照顾)|冲动|不想活"
    )
    LOW_CONTEXT = re.compile(r"没睡好|有些烦|小挫折|还能正常|现在安全|学习压力|整理.{0,8}心情")
    ACTION_DENIAL_PREFIX = re.compile(
        r"(?:没有|没|并未|未曾|从未|不会|不曾|并不|不)"
        r"(?:真的|实际|已经|正在|刚才|刚刚|刚|打算|准备|要|想|去|在|过|曾|再|了|的|\s)*$"
    )
    NO_PREPARATION = re.compile(r"没有(?:任何|具体)?(?:行动准备|实施准备|实施计划|具体计划|计划)")
    IMPULSE = re.compile(r"(?:自伤|伤害自己|轻生|自杀)的?(?:冲动|念头|想法)")
    IMMINENT_ACTION = re.compile(
        r"(?:我(?:现在|此刻|马上|今晚|今天)?(?:就|也|要|会|将|准备|打算|决定)*|"
        r"(?:现在|此刻|马上|今晚|今天)(?:我|就|要|会|将|准备|打算|决定)*)(?:动手|行动|实施)"
    )

    @classmethod
    def scoped_text(cls, text: str) -> str:
        if re.search(r"我也是(?:这个|同样)|这也是我的|我的真实|不是(?:虚构|剧情|小说)", text):
            return text
        # A source elsewhere in the message cannot determine a quote's speaker.
        # Preserve uncertain and explicitly first-person quotes. Check the local
        # introduction, stopping at the previous quote and sentence boundary.
        pieces, previous = [], 0
        for quote in cls.QUOTED.finditer(text):
            prefix = text[previous : quote.start()]
            introduction = re.split(r"[。！？.!?；;\n]", prefix)[-1]
            source = cls.QUOTE_SOURCE.search(introduction)
            own = list(cls.OWN_REPORT.finditer(introduction))
            third_party = source is not None and not (own and own[-1].start() > source.start())
            pieces.extend((prefix, "" if third_party else quote.group()))
            previous = quote.end()
        pieces.append(text[previous:])
        scoped = "".join(pieces)
        clauses = re.split(r"[，。！？,.!?；;\n]", scoped)
        # Non-self clauses are masked only with explicit attribution; anonymous
        # mentions or first-person harm outside a quote remain conservatively owned.
        return "，".join(
            c
            for c in clauses
            if not (
                cls.NONSELF.search(c.strip())
                and not (cls.OWN_ACTION.search(c) or cls.OWN_REPORT.search(c))
            )
        )

    @classmethod
    def has_concrete_action(cls, text: str) -> bool:
        # Preserve event boundaries: joining clauses can invent a time/action
        # pair. Negation is tested immediately before the matched action verb,
        # never used to discard other actions in the same clause.
        for clause in re.split(r"[。！？.!?；;\n]", text):
            clause = re.sub(r"[\s\"'“”‘’「」]+", "", clause)
            for event in cls.DANGER.finditer(clause):
                verb = next(
                    event.start(name)
                    for name in ("dose_action", "height_action", "letter_action")
                    if event.group(name) is not None
                )
                if not cls.ACTION_DENIAL_PREFIX.search(clause[:verb]):
                    return True
        return False

    def scan(self, text: str | None) -> RiskScan:
        if not text or not text.strip():
            return RiskScan(False, 0.0, (), "continue_normal_flow")
        text = text.lower()
        scoped = self.scoped_text(text)
        unnegated = self.DENIAL.sub("", scoped)
        impulse_evidence = self.IMPULSE.sub("", unnegated)
        impulse_only = bool(
            self.NO_PREPARATION.search(scoped)
            and self.IMPULSE.search(unnegated)
            and not self.OWN_ACTION.search(impulse_evidence)
            and not self.IMMINENT_ACTION.search(impulse_evidence)
        )
        risk_evidence = impulse_evidence if impulse_only else unnegated
        normalized = re.sub(r"[\s，。！？,.!?、;；:：\"'“”‘’]+", "", risk_evidence)
        matches = tuple(
            term for term in self.HIGH_RISK_PHRASES if term in normalized or term in risk_evidence
        )
        # Evaluate actions before removing denials; don't let a denial of intent
        # erase an overdose or a preparation described in another clause.
        concrete = self.has_concrete_action(scoped)
        if concrete:
            matches += ("concrete_action_or_preparation",)
        high = bool(matches)
        attributed = (
            bool(self.ATTRIBUTION.search(text))
            and not self.OWN_REPORT.search(scoped)
            and not high
            and not self.SEVERE.search(scoped)
            and (
                any(term in text for term in self.HIGH_RISK_PHRASES)
                or bool(self.DANGER.search(text))
            )
        )
        denial = (
            bool(self.DENIAL.search(scoped))
            and bool(self.LOW_CONTEXT.search(scoped))
            and not (high or self.SEVERE.search(unnegated))
        )
        return RiskScan(
            high,
            1.0 if concrete else (0.62 if high else 0.0),
            matches,
            "trigger_crisis_workflow" if high else "continue_normal_flow",
            attributed,
            denial,
            impulse_only and not high,
        )
