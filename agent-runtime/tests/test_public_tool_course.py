"""Only fixtures here are handwritten; training records must come from pinned publishers."""

import copy
import hashlib
import json

import pytest

from training.public_tools.contract import metrics, render
from training.public_tools.convert import TOOLACE_ANCHOR, convert_record, run
from training.public_tools.course import assign_components, build, decisions, load
from training.public_tools.fetch import SOURCES
from training.public_tools.train import checkpoint_state, encode


def episode(name="Read Ledger", query="Read A.", index=0, following=None):
    tools = [{"name": name, "description": "Read ledger.", "parameters": {"type": "object"}}]
    messages = [{"from": "user", "value": query},
                {"from": "assistant", "value": f'[{name}(key="TARGET_SECRET")]'}]
    messages.extend(following or [])
    return convert_record(SOURCES[0], {"system": TOOLACE_ANCHOR + json.dumps(tools),
                                      "conversations": messages}, index)


def test_target_event_arguments_and_metadata_are_absent_from_prompt():
    r = decisions(episode())[0]
    prompt = render(r, 0)
    assert "TARGET_SECRET" not in prompt and r["source"]["revision"] not in prompt
    assert r["id"] not in prompt and "targetIndices" not in prompt
    assert r["context"] == [{"role": "user", "content": "Read A.", "sourceTurn": 0}]


def test_text_action_does_not_invent_finish_or_clarify_semantics():
    raw = episode(following=[{"from": "tool", "value": '{"key":"A","value":7}'},
                             {"from": "assistant", "value": "Would you like details?"}])
    r = decisions(raw)[-1]
    assert r["observedAction"] == "assistant_text"
    assert r["targetIndices"] == [len(r["candidateTools"]) - 1]
    assert r["finishOrClarifyLabel"] is None and r["absoluteApplicabilityLabels"] is None
    assert "\"value\":7" in render(r, 0).replace("\\", "")


def test_multiple_source_calls_supervise_all_selected_distinct_tools():
    raw = episode()
    raw["tools"].append({"name": "Other", "parameters": {"type": "object"}})
    raw["events"][1]["calls"].extend([{"name": "Other", "arguments": {}},
                                      {"name": "Read Ledger", "arguments": {}}])
    assert decisions(raw)[0]["targetIndices"] == [0, 1]


def test_shared_tool_and_initial_query_are_isolated_without_splitting_episodes():
    rows = [episode("Tool A", "Q1", 0), episode("tool a", "Q2", 1),
            episode("Tool B", "Q2", 2), episode("Tool C", "Q3", 3)]
    assignments, audit = assign_components(rows, 12)
    assert len({assignments[r["id"]] for r in rows[:3]}) == 1
    assert audit["largestComponentRecords"] == 3
    assert assign_components(rows, 12) == (assignments, audit)


def test_demonstration_metrics_exclude_trivial_single_action_rows():
    a = decisions(episode())[0]
    a["scores"] = [3, 1]
    b = {**copy.deepcopy(a), "informativeChoice": False, "scores": [0], "targetIndices": [0]}
    m = metrics([a, b])
    assert m["all"]["rows"] == 2 and m["informative"]["rows"] == 1
    assert m["singleActionOnlyRows"] == 1


def test_invalid_source_and_virtual_think_are_rejected():
    raw = episode()
    raw["strictCallParsingPassed"] = False
    with pytest.raises(ValueError, match="strictly_parsed"):
        decisions(raw)
    raw["strictCallParsingPassed"] = True
    raw["tools"].append({"name": "think", "parameters": {}})
    with pytest.raises(ValueError, match="virtual_reasoning"):
        decisions(raw)


def test_length_preflight_does_not_truncate_or_trim_the_candidate_catalog():
    row = decisions(episode())[0]

    class Tokenizer:
        def __call__(self, texts, **kwargs):
            assert kwargs == {"add_special_tokens": False, "padding": False, "truncation": False}
            assert len(texts) == len(row["candidateTools"])
            return {"input_ids": [[1] * 15, [1] * 20]}

    result, audit = encode(Tokenizer(), row, 19, 100)
    assert audit["excludedReason"] == "context_budget" and len(result["input_ids"][1]) == 20
    _, audit = encode(Tokenizer(), row, 20, 39)
    assert audit["excludedReason"] == "candidate_batch_budget"
    _, audit = encode(Tokenizer(), row, 20, 40)
    assert audit["excludedReason"] is None


def test_checkpoint_identity_and_checksum_are_verified_before_loading_state(tmp_path):
    class Torch:
        def load(self, *args, **kwargs):
            raise AssertionError("Unverified state must never be loaded")

    path = tmp_path / "checkpoint.json"
    manifest = {"identity": "different-run", "artifactSha256": {}}
    path.write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match="identity_mismatch"):
        checkpoint_state(Torch(), tmp_path, "expected-run")
    manifest.update(identity="expected-run", artifactSha256={"state.pt": "wrong-hash"})
    path.write_text(json.dumps(manifest))
    (tmp_path / "state.pt").write_bytes(b"modified state")
    with pytest.raises(ValueError, match="checksum_mismatch"):
        checkpoint_state(Torch(), tmp_path, "expected-run")
    manifest["artifactSha256"] = {"../outside": "invalid"}
    path.write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match="invalid_checkpoint_path"):
        checkpoint_state(Torch(), tmp_path, "expected-run")


def test_course_end_to_end_and_future_context_rejected_even_with_updated_hash(tmp_path):
    raw, converted, out = tmp_path / "raw", tmp_path / "converted", tmp_path / "course"
    raw.mkdir()
    sources = []
    for source in SOURCES:
        folder = raw / "raw" / source["key"]
        folder.mkdir(parents=True)
        records = [episode(f"Tool {i}", f"Question {i}", i)["original"] for i in range(6)]
        if source["key"] != "toolace":
            records = [{"system": "Policy", "tools": "[]", "conversations": [
                {"from": "human", "value": "Hi"}, {"from": "gpt", "value": "Hello"}]}]
        (folder / source["dataFile"]).write_text(json.dumps(records))
        (folder / "README.md").write_text("Test fixtures, not publisher data")
        files = {str(p.relative_to(raw)): {"sha256": hashlib.sha256(p.read_bytes()).hexdigest(),
                                          "bytes": p.stat().st_size} for p in folder.iterdir()}
        sources.append({**source, "files": files})
    (raw / "manifest.json").write_text(json.dumps({"status": "download_complete", "sources": sources}))
    run(raw, converted)
    manifest = build(raw, converted, out)
    assert manifest["episodes"] == 6 and manifest["sourcePolicy"] == "primary_apache"
    assert manifest["newQuestionsGenerated"] == 0 and not manifest["semanticGoldInferred"]
    _, parts = load(out)
    assert all(parts.values())
    row = parts["train"][0]
    row["context"][0]["sourceTurn"] = row["sourceTurn"]
    p = out / "train.jsonl"
    p.write_text("".join(json.dumps(r) + "\n" for r in parts["train"]))
    manifest["artifactSha256"][p.name] = hashlib.sha256(p.read_bytes()).hexdigest()
    (out / "manifest.json").write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match="future_event"):
        load(out)
