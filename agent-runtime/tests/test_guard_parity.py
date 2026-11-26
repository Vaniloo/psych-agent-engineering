import json
from pathlib import Path

import pytest

from psych_agent_runtime.guard import assess
from psych_agent_runtime.safety import SafetyScanner

FIXTURES = json.loads(
    (Path(__file__).parents[2] / "contracts/decision-guard-cases.json").read_text()
)


@pytest.mark.parametrize("case", FIXTURES["risk"])
def test_source_action_and_denial_parity_fixture(case):
    assert SafetyScanner().scan(case["text"]).high_risk == case["highRisk"]


class Reviewer:
    def __init__(self, band, professional=False):
        self.answer = {"risk_band": band, "needs_professional": professional}

    async def chat_json(self, messages, *, purpose):
        return self.answer


@pytest.mark.asyncio
async def test_denial_keeps_recent_own_risk_but_not_a_quoted_friend():
    message = "我现在安全，没有自杀想法，只想聊聊小挫折。"
    own = [{"role": "user", "content": "我刚才有伤害自己的冲动，担心撑不住。"}]
    quoted = [{"role": "user", "content": "我在引用朋友的话：“我想死”，这是朋友的情况。"}]
    assert (await assess(message, own, Reviewer("elevated", True))).level == "professional"
    assert (await assess(message, quoted, Reviewer("elevated", True))).level == "ordinary"


@pytest.mark.asyncio
@pytest.mark.parametrize("band,want", [("urgent", "crisis"), ("elevated", "professional"), ("low", "ordinary")])
async def test_model_band_maps_to_support_level(band, want):
    assert (await assess("最近压力很大", [], Reviewer(band))).level == want


@pytest.mark.asyncio
async def test_explicit_statement_is_crisis_even_if_the_model_says_low():
    decision = await assess("我准备去死", [], Reviewer("low"))
    assert decision.level == "crisis" and decision.provider == "rules"


@pytest.mark.asyncio
async def test_review_outage_falls_back_to_the_deterministic_scan():
    class Down:
        async def chat_json(self, *a, **k):
            raise RuntimeError("offline")
    decision = await assess("有点烦", [], Down())
    assert decision.level == "ordinary" and decision.reason == "model_unavailable"
    impulse = await assess("我有伤害自己的冲动，没有行动准备，希望找专业帮助。", [], Down())
    assert impulse.level == "professional"
