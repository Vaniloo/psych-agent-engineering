"""Group/category-weighted temperatures and fail-closed category acceptance.

Uses calibration for temperature, validation for thresholds, neither test nor
development challenges for fitting. Declared groups are not proven independent.
Policies are engineering proposals requiring separate release review.
"""

import math
from collections import Counter, defaultdict

from .train_decisions import scaled

BUCKETS = {
    "risk_band": ("low", "elevated", "urgent"),
    "goal_action": ("none", "matched"),
    "goal_outcome": ("none", "partial", "done", "blocked", "rest"),
}


def bucket(task, choice):
    if task == "goal_action" and choice.startswith("action_"):
        return "matched"
    if choice not in BUCKETS[task]:
        raise ValueError("unknown calibration category")
    return choice


def critical_error(task, choice, expected):
    return choice != expected and (task == "risk_band" or choice != "none")


def wilson_upper(errors, total):
    if total == 0:
        return 1.0
    z = 1.645
    fraction = errors / total
    return (
        fraction
        + z * z / (2 * total)
        + z * math.sqrt(fraction * (1 - fraction) / total + z * z / (4 * total * total))
    ) / (1 + z * z / total)


def balanced_nll(task, rows, temperature):
    groups = defaultdict(list)
    for row in rows:
        groups[(bucket(task, row["expected"]), row["group"])].append(row)
    categories = defaultdict(list)
    for (category, _), examples in groups.items():
        losses = [
            -math.log(max(scaled(row["probabilities"], temperature)[row["expected"]], 1e-30))
            for row in examples
        ]
        categories[category].append(sum(losses) / len(losses))
    return sum(sum(values) / len(values) for values in categories.values()) / len(categories)


def category_metrics(task, rows, temperature, threshold, category):
    groups = defaultdict(list)
    for row in rows:
        probs = scaled(row["probabilities"], temperature)
        choice = row["choice"]
        if bucket(task, choice) == category and probs[choice] >= threshold:
            groups[row["group"]].append(
                (choice != row["expected"], critical_error(task, choice, row["expected"]))
            )
    errors = sum(any(error for error, _ in examples) for examples in groups.values())
    critical = sum(any(error for _, error in examples) for examples in groups.values())
    return {
        "acceptedRecords": sum(len(rows) for rows in groups.values()),
        "acceptedDeclaredGroups": len(groups),
        "groupsWithAnyError": errors,
        "groupsWithCriticalError": critical,
        "declaredGroupWilsonUpperError": wilson_upper(errors, len(groups)),
    }


def fit(task, calibration, validation, *, min_groups=20):
    if not calibration or not validation:
        raise ValueError("empty calibration or validation")
    for row in calibration + validation:
        if not row["group"] or row["expected"] not in row["probabilities"]:
            raise ValueError("invalid calibration record")
        probs = row["probabilities"]
        if any(not math.isfinite(p) or not 0 <= p <= 1 for p in probs.values()) or not math.isclose(
            sum(probs.values()), 1, abs_tol=0.002
        ):
            raise ValueError("invalid calibration probabilities")
        if row["choice"] not in probs or probs[row["choice"]] + 0.0002 < max(probs.values()):
            raise ValueError("choice disagrees with probabilities")
    if {row["group"] for row in calibration} & {row["group"] for row in validation}:
        raise ValueError("calibration/validation group overlap")
    temperatures = [0.5 + i * 0.05 for i in range(91)]
    temperature = min(temperatures, key=lambda t: balanced_nll(task, calibration, t))
    support = {
        split: {
            category: len(
                {row["group"] for row in rows if bucket(task, row["expected"]) == category}
            )
            for category in BUCKETS[task]
        }
        for split, rows in [("calibration", calibration), ("validation", validation)]
    }
    essential = BUCKETS[task] if task != "goal_outcome" else ("none", "partial", "done")
    contrasts_supported = all(
        support[split][category] >= 10 for split in support for category in essential
    )
    class_policies = {}
    for category in BUCKETS[task]:
        best = {
            "enabled": False,
            "threshold": 1.0,
            "reason": "insufficient_or_unsafe_category_validation",
        }
        if not contrasts_supported or any(support[split][category] < 10 for split in support):
            class_policies[category] = {
                **best,
                "reason": "insufficient_true_category_or_contrast_groups",
            }
            continue
        for threshold in [0.85 + i * 0.005 for i in range(31)]:
            m = category_metrics(task, validation, temperature, threshold, category)
            if (
                m["acceptedDeclaredGroups"] >= min_groups
                and m["declaredGroupWilsonUpperError"] <= 0.10
                and m["groupsWithCriticalError"] == 0
            ):
                best = {
                    "enabled": True,
                    "threshold": threshold,
                    "reason": "project_policy_category_validation",
                    **m,
                }
                break
        if not best["enabled"]:
            best["atMinimumFloor"] = category_metrics(task, validation, temperature, 0.85, category)
        class_policies[category] = best
    return {
        "enabled": any(p["enabled"] for p in class_policies.values()),
        "calibrated": True,
        "temperature": temperature,
        "threshold": 0.85,
        "classPolicies": class_policies,
        "validationScope": "project_policy_group_category_validation_not_independent_human_review",
        "independentReviewVerified": False,
        "fitScope": "Calibration NLL averaged per declared group and target category; thresholds selected on validation only.",
        "calibrationCategories": dict(
            Counter(bucket(task, row["expected"]) for row in calibration)
        ),
        "validationCategories": dict(Counter(bucket(task, row["expected"]) for row in validation)),
        "declaredTrueCategoryGroupSupport": support,
        "limits": "Wilson calculation assumes independent groups, which is not established; no public-launch guarantee. Disabled categories cannot be accepted even at probability 1.",
    }


def acceptance(task, choice, probabilities, policy, minimum=0.85):
    category = policy.get("classPolicies", {}).get(bucket(task, choice))
    if category is None:
        raise ValueError("missing category policy")
    return bool(
        policy["enabled"]
        and policy["calibrated"]
        and category["enabled"]
        and probabilities[choice] >= max(minimum, category["threshold"], policy["threshold"])
    )
