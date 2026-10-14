import sys
from types import SimpleNamespace

import pytest

import training.unrounded_laya as module
from training.train_decisions import scaled


def test_rounding_erases_small_probabilities_needed_for_temperature_scaling():
    raw = module.full_distribution([10.0, *([0.0] * 7)])
    assert all(raw[n] > 0 and round(raw[n], 4) == 0 for n in range(1, 8))
    labels = [str(i) for i in range(8)]
    precise = dict(zip(labels, raw, strict=True))
    rounded = {k: round(v, 4) for k, v in precise.items()}
    assert scaled(precise, 5.0)["0"] < 0.85
    assert scaled(rounded, 5.0)["0"] > 0.999
    assert module.full_distribution([0.0, 10.0], [1, 0])[0] > 0.9999


def test_wrapper_preserves_upstream_answers_and_adds_unrounded_distribution(monkeypatch):
    monkeypatch.setattr(module, "version", lambda _: "0.4.1")
    monkeypatch.setitem(
        sys.modules,
        "laya.agent",
        SimpleNamespace(_option_logits=lambda logits, items, offset: logits),
    )

    class Agent:
        def __init__(self):
            self.temperature = [1.0, 1.0, 1.0]
            self.temperature_by_options = {}

        def _decode_answers(self, logits, act, items, ids, internal, offset, lang=None):
            return {"task": {"choice": "yes", "probabilities": {"yes": 1.0, "no": 0.0}}}

    a = module.enable_unrounded_decoding(Agent())
    answer = a._decode_answers(
        [[12.0, 0.0]],
        None,
        [{}],
        ["task"],
        {"task": {"t": "choice", "crit": {"yes": "", "no": ""}}},
        0,
    )["task"]
    assert answer["probabilities"] == {"yes": 1.0, "no": 0.0}
    assert 0.0 < answer["unroundedProbabilities"]["no"] < 0.00005
    assert sum(answer["unroundedProbabilities"].values()) == pytest.approx(1.0)


def test_precision_path_rejects_unsupported_version_and_invalid_logits(monkeypatch):
    monkeypatch.setattr(module, "version", lambda _: "different")
    with pytest.raises(ValueError, match="0.4.1"):
        module.enable_unrounded_decoding(SimpleNamespace())
    with pytest.raises(ValueError, match="logits"):
        module.full_distribution([float("nan")])
    with pytest.raises(ValueError, match="permutation"):
        module.full_distribution([1.0, 2.0], [0, 0])


def test_training_calibration_explicitly_reads_precise_channel():
    from training.train_decisions import infer

    rounded = {"yes": 1.0, "no": 0.0}
    precise = {"yes": 0.999994, "no": 0.000006}
    a = SimpleNamespace(
        predict=lambda *_: {
            "answers": {"task": {"probabilities": rounded, "unroundedProbabilities": precise}}
        }
    )
    rows = [
        {
            "state": {},
            "expected": {"task": "yes"},
            "questions": {"task": {"criteria": {"yes": "", "no": ""}}},
        }
    ]
    assert infer(a, rows)["task"][0][0] == rounded
    assert infer(a, rows, unrounded=True)["task"][0][0] == precise
