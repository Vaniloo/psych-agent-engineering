import pytest

from training.calibration_ablation import invert, replay
from training.train_decisions import scaled


def test_inverse_temperature_roundtrip_preserves_extreme_tails():
    raw = {"yes": 0.97, "no": 0.03}
    for temperature in (0.5, 1.5, 5):
        policy = {"calibrated": True, "temperature": temperature}
        assert invert(scaled(raw, temperature), policy) == pytest.approx(raw)
    raw = {"yes": 1.0, "no": 1e-30}
    assert invert(scaled(raw, 0.5), {"calibrated": True, "temperature": 0.5}) == pytest.approx(raw)


def test_policy_cross_can_change_acceptance_but_never_label_and_keeps_floor():
    policy = {"enabled": True, "calibrated": True, "temperature": 0.5, "threshold": 0.5}
    row = {
        "expected": {"task": "no"},
        "decisions": {
            "task": {"choice": "yes", "probabilities": scaled({"yes": 0.8, "no": 0.2}, 0.5)}
        },
        "application": {"correct": True},
    }
    base, _ = replay([row], {"task": policy}, {"task": policy})
    crossed, _ = replay([row], {"task": policy}, {"task": {**policy, "temperature": 1}})
    assert base[0]["accepted"] and not base[0]["correct"]
    assert not crossed[0]["accepted"]
    assert crossed[0]["decisions"]["task"]["choice"] == "yes"
    assert "application" not in crossed[0]
    assert "application" in row


def test_disabled_policy_cannot_become_accepted():
    policy = {"enabled": True, "calibrated": True, "temperature": 1, "threshold": 0.5}
    row = {
        "expected": {"task": "yes"},
        "decisions": {"task": {"choice": "yes", "probabilities": {"yes": 1.0, "no": 0.0}}},
    }
    result, _ = replay([row], {"task": policy}, {"task": {**policy, "enabled": False}})
    assert not result[0]["accepted"]
