from training.build_domain_decisions import build
from training.decision_data import prepare_row


def test_curriculum_has_group_isolation_valid_masked_labels():
    for profile in ["router", "executor", "memory"]:
        parts = build(profile, 360)
        seen = set()
        for rows in parts.values():
            groups = {r["splitGroup"] for r in rows}
            assert not groups & seen
            seen |= groups
            for row in rows:
                assert prepare_row(row, profile)["questions"] == row["questions"]


def test_future_and_third_person_progress_does_not_mutate():
    rows = [
        r
        for values in build("executor", 1200).values()
        for r in values
        if "goal_outcome" in r["expected"]
    ]
    excluded = [
        r
        for r in rows
        if any(t in r["state"]["userExpression"] for t in ["明天打算", "朋友已经", "我没有"])
    ]
    assert excluded
    assert all(r["expected"] == {"goal_action": "none", "goal_outcome": "none"} for r in excluded)
