import json

import pytest

from psych_agent_runtime.qwen_memory_contract import INSTRUCTIONS, normalize, render
from training.qwen_memory_data import build, clean
from training.qwen_memory_probe import PREFIX, SUFFIX, format_pair
from training.train_qwen_memory import calibrate


def test_source_renderer_matches_frozen_relevance_and_preserves_original_dates():
    row = {
        "state": {
            "query": "我哪天跑步？",
            "candidate": {
                "role": "user",
                "content": "我在10月9日跑步。（本次记录于11月1日11时11分。）",
            },
        },
        "expected": {"relevance": "yes"},
    }
    assert (
        render("relevance", row["state"]) == PREFIX + format_pair(row, "memory_contract") + SUFFIX
    )
    assert clean(row["state"])["candidate"]["content"] == "我在10月9日跑步。"
    assert len(INSTRUCTIONS) == 7
    with pytest.raises(ValueError, match="source"):
        normalize("relevance", {"query": "q", "candidate": {"content": "fact"}})


def test_course_removes_cross_split_group_and_conflicting_labels(tmp_path):
    root = tmp_path / "datasets"
    state = {"query": "我会弹琴吗？", "candidate": {"role": "user", "content": "我会弹琴。"}}

    def row(label, family):
        return {
            "state": state,
            "expected": {"relevance": label},
            "splitGroup": family,
            "reviewed": True,
            "annotationStatus": "programmatic",
        }

    for folder in ("pair-round7-final/memory", "sufficiency-round8/memory"):
        (root / folder).mkdir(parents=True)
        for split in ("train", "calibration", "validation", "test"):
            rows = (
                [row("yes", "shared")]
                if folder.startswith("pair") and split in {"test", "train"}
                else []
            )
            (root / folder / (split + ".jsonl")).write_text(
                "".join(json.dumps(r) + "\n" for r in rows)
            )
    out = tmp_path / "course"
    build(root, [], out)
    assert not (out / "train.jsonl").read_text()
    assert len((out / "test.jsonl").read_text().splitlines()) == 1
    (root / "pair-round7-final/memory/train.jsonl").write_text(
        json.dumps(row("no", "other")) + "\n"
    )
    out2 = tmp_path / "conflict"
    build(root, [], out2)
    assert not (out2 / "test.jsonl").read_text()


def test_calibration_uses_unrounded_logits_and_does_not_accept_confident_errors():
    rows = [
        {
            "task": "relevance",
            "label": label,
            "family": f"{label}-{i}",
            "logitDifference": 1000 if label == "yes" else -1000,
            "choice": label,
        }
        for label in ("yes", "no")
        for i in range(10)
    ]
    good = calibrate(rows, rows)["relevance"]
    assert good["enabled"] and good["threshold"] == 0.85
    bad = [{**r, "choice": "yes", "logitDifference": 1000} for r in rows]
    assert not calibrate(rows, bad)["relevance"]["enabled"]
