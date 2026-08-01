from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class RiskScan:
    high_risk: bool
    score: float
    matched_terms: tuple[str, ...]
    action: str


class SafetyScanner:
    """Fast, deterministic first line of defense.

    This does not replace clinical assessment. It guarantees that explicit crisis
    language cannot be bypassed by planner/model failures.
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
    )
    IMMEDIACY_TERMS = ("现在", "马上", "今晚", "已经准备", "计划", "方法", "遗书")
    NEGATED_PHRASES = (
        "不想自杀",
        "没有自杀想法",
        "不会自杀",
        "并不想死",
        "不是想死",
    )

    def scan(self, text: str | None) -> RiskScan:
        if not text or not text.strip():
            return RiskScan(False, 0.0, (), "continue_normal_flow")

        normalized = re.sub(r"[\s，。！？,.!?、;；:：\"'“”‘’]+", "", text.lower())
        matches = tuple(term for term in self.HIGH_RISK_PHRASES if term in normalized)
        negated = any(term in normalized for term in self.NEGATED_PHRASES)
        immediacy = sum(1 for term in self.IMMEDIACY_TERMS if term in normalized)

        score = min(1.0, len(matches) * 0.62 + immediacy * 0.12)
        if negated and not any(term in normalized for term in ("计划", "已经准备", "马上")):
            score = min(score, 0.35)

        high_risk = bool(matches) and score >= 0.6
        return RiskScan(
            high_risk=high_risk,
            score=round(score, 3),
            matched_terms=matches,
            action="trigger_crisis_workflow" if high_risk else "continue_normal_flow",
        )
