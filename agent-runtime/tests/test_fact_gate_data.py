import hashlib
import json

import pytest

from training.fact_gate_data import build


def test_assertion_pilot_has_no_cross_partition_fact_family_or_source_overlap(tmp_path):
    path = tmp_path / "frozen"
    report = build(path)
    groups, texts = {}, {}
    for split, record in report["partitions"].items():
        raw = (path / f"{split}.jsonl").read_bytes()
        assert hashlib.sha256(raw).hexdigest() == record["sha256"]
        rows = [json.loads(line) for line in raw.splitlines()]
        assert {r["expected"] for r in rows} == {"yes", "no"}
        groups[split] = {r["family"] for r in rows}
        texts[split] = {r["source"] for r in rows}
    assert not any(groups[a] & groups[b] or texts[a] & texts[b]
                   for a in groups for b in groups if a != b)
    with pytest.raises(ValueError, match="overwrite"):
        build(path)
