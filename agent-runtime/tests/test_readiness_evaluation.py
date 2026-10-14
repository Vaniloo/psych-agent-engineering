import hashlib
import json

import pytest

from training.evaluate_readiness import application_probe, fingerprint, isolate, matches, summary
from training.readiness_challenge import freeze


@pytest.mark.asyncio
@pytest.mark.parametrize("own", [True, False])
async def test_risk_application_probe_preserves_history_scope(own):
    row = {
        "profile": "router",
        "state": {
            "message": "现在安全，自己没有自杀想法，刚才看到新闻。",
            "history": [
                {
                    "role": "user",
                    "content": "我已经服了过量药。" if own else "朋友说“我已经服了过量药”。",
                }
            ],
            "session_summary": "历史摘要不是当前行为的替代证据",
        },
        "applicationExpected": {"support_level": "crisis" if own else "self_help"},
    }
    decisions = {
        "route": {"choice": "chat", "accepted": True},
        "conversation_kind": {"choice": "counseling", "accepted": True},
        "risk_band": {"choice": "urgent", "accepted": True},
        "support_need": {"choice": "professional", "accepted": True},
    }
    result = await application_probe(row, decisions)
    assert result["correct"]
    assert result["supportLevel"] == row["applicationExpected"]["support_level"]


def test_source_changes_are_not_hidden_by_metadata_normalization():
    state = {"query": "宠物叫什么？", "candidate": {"role": "user", "content": "仓鼠叫栗子。"}}
    metadata = {**state, "candidate": {**state["candidate"], "sessionId": 99, "id": "x"}}
    assistant = {**state, "candidate": {**state["candidate"], "role": "assistant"}}
    assert fingerprint("memory", state) == fingerprint("memory", metadata)
    assert fingerprint("memory", state) != fingerprint("memory", assistant)


def test_exact_isolation_projects_legacy_batch_candidates(tmp_path):
    part = tmp_path / "three-laya-public-r6" / "memory"
    part.mkdir(parents=True)
    state = {"query": "宠物叫什么？", "candidate": {"role": "user", "content": "仓鼠叫栗子。"}}
    legacy = {"state": {"query": state["query"], "candidates": [state["candidate"]]}}
    (part / "train.jsonl").write_text(json.dumps(legacy))
    row = {"name": "duplicate", "profile": "memory", "state": state}
    changed = {
        **row,
        "name": "source-changed",
        "state": {
            **state,
            "candidate": {**state["candidate"], "role": "assistant"},
        },
    }
    kept, audit = isolate([row, changed], tmp_path)
    assert kept == [changed]
    assert audit["excludedNames"] == ["duplicate"]


def test_abstention_and_api_failure_are_not_accepted_errors():
    rows = [
        {
            "family": str(i),
            "critical": "urgent",
            "correct": i == 0,
            "usableCorrect": i == 0,
            "accepted": i < 2,
            "latencyMs": 1,
        }
        for i in range(3)
    ]
    rows.append({"family": "error", "errorType": "ReadTimeout"})
    result = summary(rows)
    assert result["usableCorrect"] == 1
    assert result["acceptedWrong"] == 1
    assert result["abstained"] == 1
    assert result["apiErrors"] == 1
    assert matches("clarify", ["finish", "clarify"])
    assert not matches("done", ["partial", "rest"])


def test_freeze_is_deterministic_and_refuses_overwrite(tmp_path):
    one, two = tmp_path / "one", tmp_path / "two"
    manifest = freeze(one)
    freeze(two)
    raw = (one / "challenge.jsonl").read_bytes()
    assert raw == (two / "challenge.jsonl").read_bytes()
    assert manifest["challengeSha256"] == hashlib.sha256(raw).hexdigest()
    with pytest.raises(ValueError, match="refuse to overwrite"):
        freeze(one)


@pytest.mark.asyncio
async def test_progress_probe_checks_outcome_not_just_candidate_presence(monkeypatch):
    row = {
        "profile": "executor",
        "state": {
            "userExpression": "今天写便签这一步卡住了。",
            "candidates": [
                {
                    "goalId": 1,
                    "version": 1,
                    "goalTitle": "记录",
                    "actionId": 2,
                    "actionTitle": "写便签",
                }
            ],
        },
        "applicationExpected": {"writeCandidate": True},
        "expected": {"goal_action": "action_0", "goal_outcome": "blocked"},
    }
    decisions = {
        "goal_action": {"choice": "action_0", "accepted": True},
        "goal_outcome": {"choice": "done", "accepted": True},
    }
    from unittest.mock import AsyncMock

    monkeypatch.setattr(
        "training.evaluate_readiness.select_progress",
        AsyncMock(
            return_value={
                "goalId": 1,
                "actionId": 2,
                "outcome": "done",
            }
        ),
    )
    result = await application_probe(row, decisions)
    assert result["writeCandidate"] is True
    assert result["correct"] is False
