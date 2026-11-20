"""Publisher fidelity and safe parsing, never model-gold synthesis."""

import copy
import hashlib
import json

import pytest

from training.public_tools.convert import TOOLACE_ANCHOR, convert_record, parse_toolace_calls
from training.public_tools.fetch import SOURCES, verify


def record(source, messages):
    tools = [{"name": "Lookup Ledger", "description": "Read the selected ledger.",
              "parameters": {"type": "object", "properties": {"key": {"type": "string"}},
                             "required": ["key"]}}]
    value = {"system": "Publisher instructions.", "conversations": messages}
    if source["key"] == "toolace":
        value["system"] += TOOLACE_ANCHOR + "\n" + json.dumps(tools) + ".\nPublisher suffix."
    else:
        value["tools"] = json.dumps(tools)
    return value


def test_space_apostrophe_and_path_names_nested_literals_are_parsed_without_rewriting():
    names = ["Get Coins' Data", "/lookup", "Get"]
    calls = parse_toolace_calls(
        "[Get Coins' Data(filters={'codes':['X-2'], 'enabled':true, 'limit':null}), /lookup(key=\"a)b\")]", names)
    assert calls == [{"name": "Get Coins' Data", "arguments": {
        "filters": {"codes": ["X-2"], "enabled": True, "limit": None}}},
        {"name": "/lookup", "arguments": {"key": "a)b"}}]


@pytest.mark.parametrize("text", ["[Lookup Ledger(key=1+2)]", "[Unknown(key='A')]",
    "[Lookup Ledger(**{'key':'A'})]", "[Lookup Ledger(key='A',key='B')]",
    "[Lookup Ledger(key='A')]; extra", "[Lookup Ledger(key='A'),]", "[]"])
def test_unsupported_calls_are_not_silently_relabelled(text):
    with pytest.raises((ValueError, SyntaxError)):
        parse_toolace_calls(text, ["Lookup Ledger"])


def test_source_call_text_cannot_execute_python(tmp_path):
    marker = tmp_path / "executed"
    payload = f"[Lookup Ledger(key=__import__('pathlib').Path({str(marker)!r}).touch())]"
    with pytest.raises(ValueError):
        parse_toolace_calls(payload, ["Lookup Ledger"])
    assert not marker.exists()


@pytest.mark.parametrize("source", SOURCES, ids=lambda s: s["key"])
def test_original_text_and_ambiguous_assistant_supervision_remain_intact(source):
    if source["key"] == "toolace":
        messages = [{"from": "user", "value": "Check the ledger."},
                    {"from": "assistant", "value": "Please give me its key."}]
    else:
        messages = [{"from": "human", "value": "Check the ledger."},
                    {"from": "gpt", "value": "Please give me its key."}]
    raw = record(source, messages); before = copy.deepcopy(raw)
    converted = convert_record(source, raw, 7)
    assert raw == before == converted["original"]
    assert [e["content"] for e in converted["events"]] == [m["value"] for m in messages]
    assert converted["events"][1]["finishOrClarifyLabel"] is None
    assert not converted["generatedTargetLabels"]
    assert not converted["legacyV2RankingSupervisionAvailable"]


@pytest.mark.parametrize("source", SOURCES, ids=lambda s: s["key"])
def test_only_publisher_selected_calls_are_positive_others_stay_unknown(source):
    if source["key"] == "toolace":
        messages = [{"from": "user", "value": "Read A."},
                    {"from": "assistant", "value": '[Lookup Ledger(key="A")]'}]
    else:
        messages = [{"from": "human", "value": "Read A."}, {"from": "function_call",
                    "value": '{"name":"Lookup Ledger","arguments":{"key":"A"}}'}]
    converted = convert_record(source, record(source, messages), 0)
    assert converted["strictCallParsingPassed"]
    assert converted["events"][1]["calls"] == [{"name": "Lookup Ledger", "arguments": {"key": "A"}}]
    assert converted["events"][1]["unselectedToolApplicability"] == "unknown_not_negative"


def test_unparsed_call_is_preserved_and_flagged():
    raw = record(SOURCES[0], [{"from": "user", "value": "Read A."},
                             {"from": "assistant", "value": '[Unknown(key="A")]'}])
    converted = convert_record(SOURCES[0], raw, 0)
    assert not converted["strictCallParsingPassed"] and converted["parseIssues"]
    assert converted["original"] == raw


def test_unsupported_dataset_source_is_rejected(tmp_path):
    (tmp_path / "manifest.json").write_text(json.dumps({"status": "download_complete", "sources": []}))
    with pytest.raises(ValueError, match="official_source_list_mismatch"):
        verify(tmp_path)


def test_non_python_parameter_names_keep_original_spelling():
    assert parse_toolace_calls('[Lookup Ledger(from="USD", media-type="image", $top=10)]',
                              ["Lookup Ledger"])[0]["arguments"] == {
                                  "from": "USD", "media-type": "image", "$top": 10}


def test_empty_catalog_is_preserved_without_inventing_no_tool_label():
    raw = {"system": TOOLACE_ANCHOR + "[]", "conversations": [
        {"from": "user", "value": "Find it."}, {"from": "assistant", "value": "No tools available."}]}
    row = convert_record(SOURCES[0], raw, 0)
    assert row["tools"] == [] and row["strictCallParsingPassed"]
    assert row["events"][1]["finishOrClarifyLabel"] is None


def test_opaque_schema_is_preserved_and_never_treated_as_empty_catalog():
    raw = {"system": "A publisher-specific table of tools", "conversations": [
        {"from": "user", "value": "Find it."}, {"from": "assistant", "value": "Answer."}]}
    row = convert_record(SOURCES[0], raw, 0)
    assert row["original"] == raw and row["tools"] is None
    assert not row["toolCatalogParsed"] and not row["strictCallParsingPassed"]


def test_missing_catalog_tool_remains_unresolved_not_a_selected_positive():
    raw = record(SOURCES[1], [{"from": "human", "value": "Read A."}, {"from": "function_call",
        "value": '{"name":"Absent","arguments":{"key":"A"}}'}])
    row = convert_record(SOURCES[1], raw, 0)
    assert row["events"][1]["unresolvedCalls"][0]["name"] == "Absent"
    assert "selectedToolNames" not in row["events"][1]
    assert not row["strictCallParsingPassed"]


def test_json_encoded_arguments_decode_once_with_provenance():
    content = json.dumps({"name": "Lookup Ledger", "arguments": json.dumps({"key": "A"})})
    raw = record(SOURCES[1], [{"from": "human", "value": "Read A."},
                            {"from": "function_call", "value": content}])
    row = convert_record(SOURCES[1], raw, 0)
    assert row["strictCallParsingPassed"] and row["original"] == raw
    assert row["events"][1]["content"] == content
    assert row["events"][1]["argumentsDecodedFromJsonString"]
    assert row["events"][1]["calls"][0]["arguments"] == {"key": "A"}


@pytest.mark.parametrize("source_list", [(SOURCES[0],), SOURCES], ids=["toolace", "legacy-import"])
def test_full_import_verification_rejects_changed_event_even_with_updated_file_hash(tmp_path, source_list):
    from training.public_tools.convert import run
    from training.public_tools.verify_interchange import check

    raw, out = tmp_path / "raw-input", tmp_path / "converted"
    raw.mkdir()
    sources = []
    for source in source_list:
        directory = raw / "raw" / source["key"]
        directory.mkdir(parents=True)
        role = "user" if source["key"] == "toolace" else "human"
        (directory / source["dataFile"]).write_text(json.dumps([
            record(source, [{"from": role, "value": "Read A."}])]))
        (directory / "README.md").write_text("Test fixture only; not actual publisher data.")
        files = {}
        for name in ("README.md", source["dataFile"]):
            path = directory / name
            files[str(path.relative_to(raw))] = {"sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                                                "bytes": path.stat().st_size}
        sources.append({**source, "files": files})
    (raw / "manifest.json").write_text(json.dumps({"status": "download_complete", "sources": sources}))
    run(raw, out)
    assert check(raw, out)["status"] == "passed"
    path = out / "toolace.jsonl"
    row = json.loads(path.read_text())
    row["events"][0]["content"] = "Changed content"
    path.write_text(json.dumps(row) + "\n")
    audit_path = out / "audit.json"
    audit = json.loads(audit_path.read_text())
    audit["artifactSha256"][path.name] = hashlib.sha256(path.read_bytes()).hexdigest()
    audit_path.write_text(json.dumps(audit))
    with pytest.raises(ValueError, match="original_message_not_preserved"):
        check(raw, out)
