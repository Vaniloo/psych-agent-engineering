import hashlib
import json
import sys
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from training.refine_decisions import challenge, executor, memory


def test_round_five_topics_states_and_challenge_are_separate():
    from training.refine_round import (
        TOPICS,
    )
    from training.refine_round import (
        challenge as fresh,
    )
    from training.refine_round import (
        executor as ex,
    )
    from training.refine_round import (
        memory as mem,
    )
    from training.refine_round import (
        router as rt,
    )

    topics = set()
    for values in TOPICS.values():
        assert not topics.intersection(values)
        topics.update(values)
    held = {digest(c["state"]) for c in fresh()}
    for generator in [rt, ex, mem]:
        seen = set(held)
        for split in ["train", "calibration", "validation", "test"]:
            states = {digest(r["state"]) for r in generator(split)}
            assert not seen.intersection(states)
            seen |= states


def test_recent_risk_denial_stays_cautious_and_preparation_stays_urgent():
    from training.refine_round import router

    rows = router("train")
    recent = [r for r in rows if r["state"]["history"]]
    assert recent and all(r["expected"]["risk_band"] == "elevated" for r in recent)
    contradictory = [r for r in rows if "工具已经拿到" in r["state"]["message"]]
    assert contradictory and all(r["expected"]["risk_band"] == "urgent" for r in contradictory)


def digest(state):
    return hashlib.sha256(
        json.dumps(state, sort_keys=True, ensure_ascii=False).encode()
    ).hexdigest()


def test_boundary_families_and_frozen_challenge_do_not_leak():
    held = {digest(c["state"]) for c in challenge()}
    for generator in [executor, memory]:
        seen = set(held)
        for split in ["train", "calibration", "validation", "test"]:
            states = {digest(r["state"]) for r in generator(split)}
            assert not states & seen
            seen |= states


def test_curriculum_excludes_future_and_others_from_progress():
    rows = [r for r in executor("train") if "goal_outcome" in r["expected"]]
    excluded = [r for r in rows if r["splitGroup"].endswith(("-future", "-other"))]
    assert excluded
    assert all(r["expected"] == {"goal_action": "none", "goal_outcome": "none"} for r in excluded)


def test_memory_incomplete_compound_query_requires_more_evidence():
    rows = [r for r in memory("validation") if "v3-incomplete" in r["splitGroup"]]
    assert len(rows) == 6
    assert all(r["expected"]["evidence_sufficient"] == "no" for r in rows)


def test_fresh_executor_challenge_is_disjoint_from_refinement_splits():
    from training.refine_executor_safety import authored, fresh_challenge

    held = {digest(c["state"]) for c in fresh_challenge()}
    seen = set(held)
    for split in ["train", "calibration", "validation", "test"]:
        rows = authored(split) + [
            row for row in executor(split) if "next_action" in row["expected"]
        ]
        states = {digest(r["state"]) for r in rows}
        assert not states & seen
        seen |= states


def test_rejected_checkpoint_cannot_be_served(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from psych_agent_runtime.routing_service import create_app

    (tmp_path / "deployment.json").write_text(json.dumps({"promotionStatus": "rejected"}))
    load = Mock()
    monkeypatch.setitem(sys.modules, "laya", SimpleNamespace(load=load))
    monkeypatch.setitem(sys.modules, "torch", SimpleNamespace())
    monkeypatch.setenv("DECISION_CHECKPOINT", str(tmp_path))
    monkeypatch.setenv("DECISION_PROFILE", "executor")
    with pytest.raises(ValueError, match="rejected"), TestClient(create_app()):
        pass
    load.assert_not_called()


def test_teacher_completed_sentence_cannot_enter_as_partial(tmp_path):
    from training.build_domain_decisions import build, row
    from training.refine_executor_safety import build as build_safety

    baseline = tmp_path / "baseline"
    baseline.mkdir()
    for split, rows in build("executor", 360).items():
        (baseline / f"{split}.jsonl").write_text(
            "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows)
        )
    state = {
        "userExpression": "我已经把观点写成完整的一句话了。",
        "candidates": [{"goalTitle": "表达想法", "actionTitle": "写一句观点"}],
    }
    invalid = row(
        "executor",
        state,
        {"goal_action": "action_0", "goal_outcome": "partial"},
        "teacher-conflict",
    )
    invalid.update(reviewed=False, annotationStatus="teacher_draft")
    teacher = tmp_path / "teacher.jsonl"
    teacher.write_text(json.dumps(invalid, ensure_ascii=False) + "\n")
    output = tmp_path / "filtered"
    counts = build_safety(baseline, output, teacher)
    assert counts["train"]["teacherDrafts"] == 0
    assert (
        json.loads((output / "teacher-exclusions.json").read_text())[0]["splitGroup"]
        == "teacher-conflict"
    )
