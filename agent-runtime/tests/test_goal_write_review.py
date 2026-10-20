import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from psych_agent_runtime.deepseek_client import DeepSeekUnavailable
from psych_agent_runtime.goal_write_review import GoalWriteReviewer

STATE = {"message": "头一项终于弄妥了", "history": [], "candidates": [{
    "goalId": "PRIVATE_GOAL_ID", "goalTitle": "写博客", "actionTitle": "列要点",
    "position": 1, "focused": True, "status": "planned"}]}
PROPOSAL = {"operation": "progress", "candidateIndex": 0, "outcome": "done"}


@pytest.mark.asyncio
@pytest.mark.parametrize("answer,status", [
    ({"decision": "confirm", "candidateIndex": 0, "outcome": "done", "evidence": STATE["message"]}, "confirmed"),
    ({"decision": "confirm", "candidateIndex": 1, "outcome": "done", "evidence": STATE["message"]}, "rejected"),
    ({"decision": "confirm", "candidateIndex": 0, "outcome": "partial", "evidence": STATE["message"]}, "rejected"),
    ({"decision": "confirm", "candidateIndex": True, "outcome": "done", "evidence": STATE["message"]}, "rejected"),
    ({"decision": "confirm", "candidateIndex": 0, "outcome": "done", "evidence": "助手说完成了"}, "unavailable"),
    ({"decision": "clarify"}, "clarify"), ({"decision": "reject"}, "rejected"),
    ({"decision": "yes"}, "unavailable"),
])
async def test_write_review_requires_independent_agreement_and_real_current_source(answer, status):
    model = SimpleNamespace(chat_json=AsyncMock(return_value=answer))
    assert (await GoalWriteReviewer(model).verify(STATE, PROPOSAL))["status"] == status
    prompt = json.loads(model.chat_json.call_args.args[0][-1]["content"])
    assert prompt["current"]["message"] == STATE["message"]
    assert "PRIVATE_GOAL_ID" not in json.dumps(prompt)
    assert model.chat_json.call_args.kwargs["purpose"] == "goal_write_review"


@pytest.mark.asyncio
async def test_write_review_stops_reusing_auth_or_quota_failure_until_restart():
    model = SimpleNamespace(chat_json=AsyncMock(side_effect=DeepSeekUnavailable(401)))
    reviewer = GoalWriteReviewer(model)
    assert (await reviewer.verify(STATE, PROPOSAL))["status"] == "unavailable"
    assert (await reviewer.verify(STATE, PROPOSAL))["status"] == "unavailable"
    model.chat_json.assert_awaited_once()


@pytest.mark.asyncio
async def test_resolution_chooses_only_an_existing_planned_step_and_allowed_outcome():
    proposal = {"operation": "progress", "candidateIndex": None, "outcome": None}
    model = SimpleNamespace(chat_json=AsyncMock(return_value={"decision": "confirm", "candidateIndex": 0,
        "outcome": "partial", "evidence": STATE["message"]}))
    reviewer = GoalWriteReviewer(model)
    assert await reviewer.verify(STATE, proposal) == {"status": "confirmed", "candidateIndex": 0, "outcome": "partial"}
    changed = {**STATE, "candidates": [{**STATE["candidates"][0], "status": "done"}]}
    assert (await reviewer.verify(changed, proposal))["status"] == "rejected"
    model.chat_json.return_value["candidateIndex"] = 9
    assert (await reviewer.verify(STATE, proposal))["status"] == "rejected"
