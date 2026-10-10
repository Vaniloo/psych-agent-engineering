import copy

import pytest

from training.promotion_gate import compare


def reports(role="executor", expected=None):
    row = {
        "profile": role,
        "name": "protected",
        "expected": expected or {"goal_action": "none"},
        "actual": expected or {"goal_action": "none"},
        "accepted": True,
        "correct": True,
        "usableCorrect": True,
    }
    report = {
        "challengeSha256": "same",
        "cases": [row],
        "summary": {role: {"usableCorrect": 1, "acceptedWrong": 0}},
    }
    return report, copy.deepcopy(report)


def test_gate_rejects_mismatched_challenge_or_case_inventory():
    old, new = reports()
    new["challengeSha256"] = "different"
    with pytest.raises(ValueError, match="same frozen"):
        compare(old, new)
    new["challengeSha256"] = "same"
    new["cases"] = []
    with pytest.raises(ValueError, match="identical cases"):
        compare(old, new)


def test_old_score_improvement_does_not_allow_new_false_authorization():
    old, new = reports()
    new["cases"][0].update(actual={"goal_action": "action_0"}, correct=False)
    new["summary"]["executor"]["usableCorrect"] = 2
    assert "new false action authorization" in compare(old, new)["executor"]["reasons"][0]


def test_total_improvement_does_not_hide_memory_regression():
    old, new = reports("memory", {"episodic": "no", "semantic": "yes"})
    new["cases"][0].update(actual={"episodic": "yes", "semantic": "yes"}, correct=False)
    new["summary"]["memory"]["usableCorrect"] = 2
    assert not compare(old, new)["memory"]["passed"]


def test_gate_rejects_lower_usable_coverage_and_unaccepted_urgent_case():
    old, new = reports()
    new["summary"]["executor"]["usableCorrect"] = 0
    assert not compare(old, new)["executor"]["passed"]
    old, new = reports("router", {"risk_band": "urgent"})
    new["cases"][0]["usableCorrect"] = False
    assert not compare(old, new)["router"]["passed"]


def test_unchanged_reports_pass():
    old, new = reports()
    assert compare(old, new)["executor"]["passed"]


def test_aggregate_improvement_cannot_hide_new_accepted_elevated_downgrade():
    old, new = reports("router", {"risk_band": "elevated"})
    new["cases"][0].update(actual={"risk_band": "low"}, correct=False, usableCorrect=False)
    new["summary"]["router"]["usableCorrect"] = 2
    assert "risk downgrade" in compare(old, new)["router"]["reasons"][0]
