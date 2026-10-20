"""Soft candidate diversity from existing, accepted JEV-style write-time labels.

No new query classifier or write permission. At least three quarters of the
original candidate budget remain unchanged; unknown labels stay searchable.
"""

import json
import math

from .bm25 import BM25

TYPES = ("episodic", "semantic", "preference", "procedural")
POLICY = "memory-type-diversity-v1"


def accepted_types(value):
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (ValueError, TypeError):
            return []
    if not isinstance(value, dict):
        return []
    return [
        name
        for name in TYPES
        if isinstance(value.get(name), dict)
        and value[name].get("accepted") is True
        and value[name].get("choice") == "yes"
    ]


def labels(record):
    value = record.get("memoryTypes")
    return (
        [name for name in TYPES if name in value]
        if isinstance(value, list)
        else accepted_types(record.get("types"))
    )


def diversify(records, query, baseline, limit):
    if limit < 4 or len(baseline) < limit:
        return baseline[:limit]
    reserved = math.ceil(limit * 0.75)
    selected = list(baseline[:reserved])
    seen = {r["id"] for r in selected}
    ranking = BM25([r["content"] for r in records]).search(query, len(records))
    candidates = [records[h.index] for h in ranking if h.score > 0]
    while len(selected) < limit:
        represented = {name for r in selected for name in labels(r)}
        candidate = next(
            (r for r in candidates if r["id"] not in seen and set(labels(r)) - represented), None
        )
        if candidate is None:
            break
        selected.append(candidate)
        seen.add(candidate["id"])
    for row in baseline:
        if len(selected) >= limit:
            break
        if row["id"] not in seen:
            selected.append(row)
            seen.add(row["id"])
    return selected
