import json

from training.decision_data import prepare_row
from training.task_focus_curriculum import SPLITS, TARGETS, executor, memory, router


def test_targeted_fixtures_have_valid_contracts_and_disjoint_states_groups():
    for role, builder in [("router", router), ("executor", executor), ("memory", memory)]:
        seen_states, seen_groups = set(), set()
        for split in SPLITS:
            rows = builder(split)
            assert rows
            groups = {r["splitGroup"] for r in rows}
            states = {json.dumps(r["state"], sort_keys=True) for r in rows}
            assert not groups & seen_groups and not states & seen_states
            assert len(states) == len(rows)
            for r in rows:
                assert set(r["expected"]) <= set(TARGETS[role])
                assert (
                    prepare_row(r, role, contract=r["contractVersion"])["expected"] == r["expected"]
                )
                assert r["reviewedBy"] != "independent_human"
            seen_states |= states
            seen_groups |= groups


def test_permuted_events_keep_one_family_and_history_source_is_explicit():
    cases = executor("train")
    assert len({r["splitGroup"] for r in cases}) == len(cases) // 3
    assert {r["expected"]["goal_action"] for r in cases if r["semanticFamily"] == "event-0"} == {
        "action_0",
        "action_1",
        "action_2",
    }
    risk = {r["semanticFamily"]: r for r in router("test")}
    assert risk["history-own-action"]["expected"]["risk_band"] == "urgent"
    assert risk["history-own-action"]["state"]["history"][0]["role"] == "user"
    assert risk["history-other-action"]["expected"]["risk_band"] == "low"
