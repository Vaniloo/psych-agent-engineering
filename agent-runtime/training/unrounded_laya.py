"""Offline-only choice probabilities from pinned Laya 0.4.1 logits.

Preserve upstream rounded answers and append a float64 distribution. No library
files, weights, workers or deployment policies are edited. The private decoder
boundary is version checked and fails on incompatible interfaces or results.
"""

import inspect
import math
from importlib.metadata import version
from types import MethodType


def full_distribution(scores, order=None):
    scores = [float(value) for value in scores]
    if not scores or any(not math.isfinite(value) for value in scores):
        raise ValueError("invalid option logits")
    peak = max(scores)
    weights = [math.exp(value - peak) for value in scores]
    total = sum(weights)
    probabilities = [value / total for value in weights]
    if order is not None:
        if len(order) != len(scores) or set(order) != set(range(len(scores))):
            raise ValueError("invalid option permutation")
        canonical = [0.0] * len(scores)
        for slot, option in enumerate(order):
            canonical[option] = probabilities[slot]
        return canonical
    return probabilities


def enable_unrounded_decoding(agent):
    if version("laya") != "0.4.1":
        raise ValueError("unrounded decoder only validated against Laya 0.4.1")
    from laya.agent import _option_logits

    expected = ["logits", "act", "items", "ids", "internal", "offset", "lang"]
    original = agent._decode_answers
    if list(inspect.signature(original).parameters) != expected:
        raise ValueError("unsupported Laya decoder interface")
    if list(agent.temperature) != [1.0, 1.0, 1.0] or agent.temperature_by_options:
        raise ValueError("unrounded calibration requires neutral internal temperatures")

    def decode(self, logits, act, items, ids, internal, offset, lang=None):
        if lang is not None:
            raise ValueError("language overrides unsupported by offline decoder")
        answers = original(logits, act, items, ids, internal, offset, lang)
        raw = _option_logits(logits, items, offset)
        for index, name in enumerate(ids):
            question = internal[name]
            if question["t"] != "choice":
                raise ValueError("precision decoder supports choice only")
            keys = list(question["crit"])
            probabilities = full_distribution(raw[index], question.get("option_order"))
            if len(probabilities) != len(keys):
                raise ValueError("logit/option count differs")
            precise = dict(zip(keys, probabilities, strict=True))
            choice = answers[name]["choice"]
            if precise[choice] + 1e-12 < max(precise.values()) or any(
                abs(value - answers[name]["probabilities"][key]) > 0.00006
                for key, value in precise.items()
            ):
                raise ValueError("precision decoder differs from upstream answers")
            answers[name]["unroundedProbabilities"] = precise
        return answers

    agent._decode_answers = MethodType(decode, agent)
    return agent
