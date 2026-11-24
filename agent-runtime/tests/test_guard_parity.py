import json
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from benchmarks.runtime_fixture import make_runtime, risk_client

from psych_agent_runtime.route_policy import plan_for_route
from psych_agent_runtime.safety import SafetyScanner

FIXTURES = json.loads(
    (Path(__file__).parents[2] / "contracts/decision-guard-cases.json").read_text()
)


@pytest.mark.parametrize("case", FIXTURES["risk"])
def test_source_action_and_denial_parity_fixture(case):
    assert SafetyScanner().scan(case["text"]).high_risk == case["highRisk"]


@pytest.mark.asyncio
async def test_denial_preserves_recent_owned_risk_but_not_friend_quote():
    runtime = make_runtime()
    runtime._emit = lambda *args: None
    runtime.planner = AsyncMock()
    runtime.decision_client = risk_client("elevated", True)
    routing = {
        "conversationKind": "counseling",
        "kindAccepted": True,
    }
    message = "我现在安全，没有自杀想法，只想聊聊小挫折。"
    for history, want in [
        (
            [{"role": "user", "content": "我刚才有伤害自己的冲动，担心撑不住。"}],
            "professional_support",
        ),
        (
            [{"role": "user", "content": "我在引用朋友的话：“我想死”，这是朋友的情况。"}],
            "self_help",
        ),
    ]:
        result = await runtime._risk_decision(
            {
                "message": message,
                "plan": plan_for_route("chat", message, "test", routing).model_dump(),
                "context": {"username": "fixture", "sessionId": 1, "messages": history},
            }
        )
        assert result["support_level"] == want


@pytest.mark.asyncio
@pytest.mark.parametrize("recent", [False, True])
async def test_impulse_guard_keeps_professional_support_and_recent_urgent_risk(recent):
    runtime = make_runtime()
    runtime._emit = lambda *args: None
    message = "我有伤害自己的冲动，没有行动准备，希望找专业帮助。"
    routing = {
        "conversationKind": "counseling",
        "kindAccepted": True,
    }
    runtime.decision_client = risk_client("urgent", True)
    history = [{"role": "user", "content": "我刚服用了过量的药。"}] if recent else []
    result = await runtime._risk_decision(
        {
            "message": message,
            "plan": plan_for_route("chat", message, "test", routing).model_dump(),
            "context": {"username": "fixture", "sessionId": 1, "messages": history},
        }
    )
    assert result["support_level"] == ("crisis" if recent else "professional_support")
