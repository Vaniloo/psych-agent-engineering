import json
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from psych_agent_runtime.goal_creation import explicit_creation
from psych_agent_runtime.goal_evidence import supports_progress
from psych_agent_runtime.graph import AgentGraphRuntime
from psych_agent_runtime.route_policy import plan_for_route
from psych_agent_runtime.safety import SafetyScanner

FIXTURES = json.loads(
    (Path(__file__).parents[2] / "contracts/decision-guard-cases.json").read_text()
)


@pytest.mark.parametrize("case", FIXTURES["risk"])
def test_source_action_and_denial_parity_fixture(case):
    assert SafetyScanner().scan(case["text"]).high_risk == case["highRisk"]


@pytest.mark.parametrize("case", FIXTURES["progress"])
def test_goal_policy_parity_fixture(case):
    assert supports_progress(case["text"], case["title"], case["outcome"]) == case["allowed"]


@pytest.mark.parametrize("case", FIXTURES["creation"])
def test_creation_policy_parity_fixture(case):
    assert explicit_creation(case["text"]) == case["allowed"]


@pytest.mark.asyncio
async def test_denial_preserves_recent_owned_risk_but_not_friend_quote():
    runtime = AgentGraphRuntime.__new__(AgentGraphRuntime)
    runtime._emit = lambda *args: None
    runtime.planner = AsyncMock()
    routing = {
        "conversationKind": "counseling",
        "kindAccepted": True,
        "riskDecision": {
            "riskBand": "elevated",
            "accepted": True,
            "needsProfessionalSupport": True,
        },
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


def test_past_creation_is_not_new_authorization():
    assert not explicit_creation("我上次已经让你建立了一个目标，今天只是回顾。")
    assert explicit_creation("现在请为我创建一个规律阅读的目标。")


@pytest.mark.asyncio
@pytest.mark.parametrize("recent", [False, True])
async def test_impulse_guard_keeps_professional_support_and_recent_urgent_risk(recent):
    runtime = AgentGraphRuntime.__new__(AgentGraphRuntime)
    runtime._emit = lambda *args: None
    message = "我有伤害自己的冲动，没有行动准备，希望找专业帮助。"
    routing = {
        "conversationKind": "counseling",
        "kindAccepted": True,
        "riskDecision": {"riskBand": "urgent", "accepted": True, "needsProfessionalSupport": True},
    }
    history = [{"role": "user", "content": "我刚服用了过量的药。"}] if recent else []
    result = await runtime._risk_decision(
        {
            "message": message,
            "plan": plan_for_route("chat", message, "test", routing).model_dump(),
            "context": {"username": "fixture", "sessionId": 1, "messages": history},
        }
    )
    assert result["support_level"] == ("crisis" if recent else "professional_support")
