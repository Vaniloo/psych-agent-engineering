from copy import deepcopy

import pytest

from training.release_readiness import review_role


def recorded(choice="none", accepted=True):
    case = {
        "name": "negative",
        "family": "f",
        "state": {},
        "expected": {"goal_action": "none", "goal_outcome": "none"},
        "applicationExpected": {"writeCandidate": False},
        "critical": "false-progress-authorization",
        "evaluationGroup": "fresh",
        "decisions": {
            "goal_action": {"choice": choice, "accepted": accepted},
            "goal_outcome": {"choice": "none", "accepted": False},
        },
        "correct": choice == "none",
        "accepted": False,
        "usableCorrect": False,
        "latencyMs": 1,
        "application": {"writeCandidate": False, "correct": True},
    }
    return {
        "health": {"model": "x", "tasks": {"goal_action": True, "goal_outcome": True}},
        "cases": [case],
    }


def test_critical_task_error_is_checked_even_if_other_task_abstains():
    before = recorded(accepted=False)
    after = recorded("action_0")
    result = review_role(before, after)
    assert not result["checks"]["noNewCriticalAcceptedErrors"]
    assert result["newCriticalErrors"][0]["task"] == "goal_action"


def test_labels_or_inputs_cannot_be_changed_to_pass_release_check():
    before = recorded()
    after = deepcopy(before)
    after["cases"][0]["expected"]["goal_action"] = "action_0"
    with pytest.raises(ValueError, match="frozen"):
        review_role(before, after)


def test_false_application_write_fails_even_when_model_label_correct():
    before = recorded()
    after = deepcopy(before)
    after["cases"][0]["application"]["writeCandidate"] = True
    assert not review_role(before, after)["checks"]["noFalseAutomaticGoalWrites"]


def test_existing_critical_error_is_not_a_public_release_allowance():
    before = recorded("action_0")
    after = deepcopy(before)
    result = review_role(before, after)
    assert result["checks"]["noNewCriticalAcceptedErrors"]
    assert not result["checks"]["noCriticalAcceptedErrors"]
    assert result["criticalAcceptedErrors"][0]["task"] == "goal_action"
    assert not result["passed"]


def test_deferred_or_failed_application_probe_blocks_release():
    before = recorded()
    for application in ({}, {"probeErrorType": "ModuleNotFoundError", "correct": False}):
        after = deepcopy(before)
        after["cases"][0]["application"] = application
        result = review_role(before, after)
        assert not result["checks"]["applicationProbesComplete"]
        assert result["missingApplicationChecks"] == ["negative"]
        assert not result["passed"]
