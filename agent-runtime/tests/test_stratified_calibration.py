import copy

import pytest

from training.stratified_calibration import acceptance, balanced_nll, fit


def examples(prefix, count=30):
    result = []
    for label in ("low", "elevated", "urgent"):
        for n in range(count):
            result.append(
                {
                    "group": f"{prefix}-{label}-{n}",
                    "expected": label,
                    "choice": label,
                    "probabilities": {
                        k: 0.98 if k == label else 0.01 for k in ("low", "elevated", "urgent")
                    },
                }
            )
    return result


def test_safe_separable_categories_enable_without_lowering_floor():
    policy = fit("risk_band", examples("cal"), examples("val"))
    assert all(p["enabled"] and p["threshold"] >= 0.85 for p in policy["classPolicies"].values())
    assert policy["independentReviewVerified"] is False
    assert not acceptance("risk_band", "low", {"low": 0.84}, policy)


def test_duplicate_records_do_not_inflate_group_support_or_nll():
    rows = examples("cal")
    assert balanced_nll("risk_band", rows, 1) == pytest.approx(
        balanced_nll("risk_band", rows + [rows[0]] * 100, 1)
    )
    validation = examples("val")
    for r in validation:
        r["group"] = "one-" + r["expected"]
    policy = fit("risk_band", rows, validation * 10)
    assert not policy["enabled"]


def test_saturated_dangerous_error_disables_category_even_if_overall_score_high():
    calibration, validation = examples("cal"), examples("val")
    bad = {
        "group": "bad",
        "expected": "low",
        "choice": "urgent",
        "probabilities": {"low": 0.0, "elevated": 0.0, "urgent": 1.0},
    }
    validation.append(bad)
    policy = fit("risk_band", calibration, validation)
    assert not policy["classPolicies"]["urgent"]["enabled"]
    assert not acceptance("risk_band", "urgent", bad["probabilities"], policy)


def test_missing_risk_contrast_blocks_passing_normal_examples():
    policy = fit(
        "risk_band", [r for r in examples("cal") if r["expected"] == "low"], examples("val")
    )
    assert not policy["enabled"]


def test_fit_rejects_group_leakage_and_invalid_probability_choice():
    with pytest.raises(ValueError, match="overlap"):
        fit("risk_band", examples("same"), examples("same"))
    rows = copy.deepcopy(examples("val"))
    rows[0]["choice"] = "urgent"
    with pytest.raises(ValueError, match="choice"):
        fit("risk_band", examples("cal"), rows)
