import json

import pytest

from psych_agent_runtime.decision_contract import PROFILES
from training.decision_data import load_parts, prepare_row, selected_questions
from training.train_decisions import fit_policy


def sample(group="one"):
    return {
        "state": {"message": group},
        "contractVersion": PROFILES["router"][0],
        "splitGroup": group,
        "expected": {"conversation_kind": "chat"},
        "reviewed": True,
    }


def test_pending_labels_rejected_and_teacher_mode_explicit():
    row = sample()
    row["reviewed"] = False
    with pytest.raises(ValueError, match="unreviewed"):
        prepare_row(row, "router")
    row["annotationStatus"] = "teacher_draft"
    assert prepare_row(row, "router", True)["expected"]["conversation_kind"] == "chat"
    row["expected"]["conversation_kind"] = "made-up"
    with pytest.raises(ValueError, match="label"):
        prepare_row(row, "router", True)


def test_split_leakage_and_exact_input_duplicate(tmp_path):
    for split in ("train", "calibration", "validation", "test"):
        (tmp_path / f"{split}.jsonl").write_text(json.dumps(sample(split)) + "\n")
    assert len(load_parts(tmp_path, "router")) == 4
    (tmp_path / "test.jsonl").write_text(json.dumps(sample("train")) + "\n")
    with pytest.raises(ValueError, match="leakage"):
        load_parts(tmp_path, "router")


def test_dynamic_permission_mask_and_candidate_labels():
    q = selected_questions("executor", {"allowedTools": ["get_dashboard"]}, ["next_action"])
    assert set(q["next_action"]["criteria"]) == {"get_dashboard", "finish", "clarify"}
    q = selected_questions(
        "executor",
        {"candidates": [{"goalTitle": "休息", "actionTitle": "走一分钟"}]},
        ["goal_action", "goal_outcome"],
    )
    assert set(q["goal_action"]["criteria"]) == {"none", "action_0"}
    assert "goal_outcome" in q


def test_teacher_training_never_enables_deployment():
    examples = [({"yes": 0.95, "no": 0.05}, "yes") for _ in range(40)]
    assert fit_policy(examples, examples, True)["enabled"] is False
    assert fit_policy(examples, examples, False)["enabled"] is True
    assert fit_policy(examples[:2], examples, False)["calibrated"] is False


def test_memory_training_grounds_each_candidate_like_serving():
    from psych_agent_runtime.decision_contract import focused_memory_questions

    state = {
        "query": "我喜欢什么？",
        "candidates": [{"content": "我喜欢散步"}, {"content": "助手建议喝水"}],
    }
    q = selected_questions("memory", state, ["relevance_0", "relevance_1"])
    assert q == focused_memory_questions(state, ["relevance_0", "relevance_1"])
    assert "我喜欢散步" in q["relevance_0"]["instructions"]
    assert "助手建议喝水" not in q["relevance_0"]["instructions"]
    with pytest.raises(ValueError, match="position"):
        selected_questions("memory", state, ["relevance_2"])
