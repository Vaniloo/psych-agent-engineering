import pytest

from training.finish_public_round import rewrite_role, same_predictions


def test_rollout_changes_only_the_selected_mapping_and_refuses_unexpected_base():
    original = (
        " router) port=18091; version=domain-v3; export CUDA_VISIBLE_DEVICES=0 ;;\n"
        " executor) port=18093; version=domain-v4; export CUDA_VISIBLE_DEVICES=0 ;;\n"
        " memory) port=18092; version=domain-v2; export CUDA_VISIBLE_DEVICES=5 ;;\n"
    )
    updated = rewrite_role(original, "memory", "domain-v2", "domain-v5")
    assert updated.splitlines()[:2] == original.splitlines()[:2]
    assert "version=domain-v5;" in updated
    assert "CUDA_VISIBLE_DEVICES=1" in updated
    with pytest.raises(ValueError, match="mapping changed"):
        rewrite_role(updated, "memory", "domain-v2", "domain-v5")


def test_live_choices_and_acceptance_must_both_match_canary():
    def report(choice, accepted, role="memory"):
        return {
            "cases": [
                {
                    "profile": role,
                    "name": "same",
                    "actual": {"semantic": choice},
                    "accepted": accepted,
                }
            ]
        }

    assert same_predictions(report("yes", True), report("yes", True), "memory")
    assert not same_predictions(report("yes", True), report("yes", False), "memory")
    assert not same_predictions(report("yes", True), report("no", True), "memory")
    assert not same_predictions(report("yes", True), report("yes", True, "executor"), "memory")
