import math
from types import SimpleNamespace

import pytest

from training.decision_data import prepare_row
from training.refine_contrasts import SPLITS, executor, router
from training.train_decisions import main


def test_contrasts_select_actual_object_only_when_it_is_an_owned_candidate():
    rows = executor("train")
    text = "我今天已经把水杯放到门口了，雨伞没碰。"
    paired = [r for r in rows if text in r["state"]["userExpression"]]
    assert len(paired) == 6
    selected = []
    for r in paired:
        prepare_row(r, "executor")
        action = r["expected"]["goal_action"]
        if action == "none":
            assert r["expected"]["goal_outcome"] == "none"
        else:
            item = r["state"]["candidates"][int(action.removeprefix("action_"))]
            selected.append(item["actionTitle"])
            assert r["expected"]["goal_outcome"] == "done"
    assert selected == ["把水杯放到门口"] * 3


def test_new_splits_have_disjoint_inputs_and_valid_dynamic_label_contracts():
    for role, builder in [("router", router), ("executor", executor)]:
        seen = set()
        for split in SPLITS:
            rows = builder(split)
            states = set()
            for r in rows:
                prepare_row(r, role)
                states.add(str(r["state"]))
            assert not (states & seen)
            seen |= states


@pytest.mark.parametrize("lr", [0, -1, math.nan, math.inf])
def test_invalid_learning_rate_fails_before_loading_model_or_data(lr):
    with pytest.raises(ValueError, match="finite and positive"):
        main(SimpleNamespace(encoder_lr=lr, head_lr=1e-4))
