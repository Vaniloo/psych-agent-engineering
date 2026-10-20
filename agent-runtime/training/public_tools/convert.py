"""Lossless source-event interchange and audit. No invented questions or decision gold."""

import argparse
import ast
import hashlib
import json
import re
from collections import Counter
from pathlib import Path

from .fetch import verify, write_json

CONTRACT = "official-tool-trajectories-v3"
TOOLACE_ANCHOR = "Here is a list of functions in JSON format that you can invoke:"
ROLE_MAP = {"user": "user", "human": "user", "assistant": "assistant", "gpt": "assistant",
            "tool": "observation", "observation": "observation", "function_call": "tool_call"}


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def digest(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def source_tools(source, record):
    if source == "toolace":
        if TOOLACE_ANCHOR not in record["system"]:
            raise ValueError("source_tool_declaration_missing")
        tools, _ = json.JSONDecoder().raw_decode(record["system"].split(TOOLACE_ANCHOR, 1)[1].lstrip())
    else:
        tools = json.loads(record["tools"]) if isinstance(record["tools"], str) else record["tools"]
    if not isinstance(tools, list):
        raise ValueError("source_tools_not_list")
    for tool in tools:
        if not isinstance(tool, dict) or not isinstance(tool.get("name"), str):
            raise ValueError("source_tool_name_invalid")
        if not isinstance(tool.get("parameters"), dict):
            raise ValueError("source_parameter_declaration_invalid")
    if len({t["name"] for t in tools}) != len(tools):
        raise ValueError("source_duplicate_tool_names")
    return tools


def literal(node):
    # Accept JSON spellings in the publisher's Python-like syntax. Never eval code.
    if isinstance(node, ast.Name) and node.id in {"true", "false", "null"}:
        return {"true": True, "false": False, "null": None}[node.id]
    if isinstance(node, ast.List):
        return [literal(v) for v in node.elts]
    if isinstance(node, ast.Dict):
        if any(k is None for k in node.keys):
            raise ValueError("unpacking_in_source_arguments")
        keys = [literal(k) for k in node.keys]
        if any(not isinstance(k, str) for k in keys) or len(set(keys)) != len(keys):
            raise ValueError("non_json_object_arguments")
        return {k: literal(v) for k, v in zip(keys, node.values, strict=True)}
    value = ast.literal_eval(node)
    if not isinstance(value, (str, int, float, bool, type(None))):
        raise ValueError("non_json_literal_arguments")
    return value


def parse_toolace_calls(text, names):
    """Strict complete call block only; names may contain spaces, apostrophes or slashes."""
    text = text.strip()
    if not text.startswith("[") or not text.endswith("]"):
        raise ValueError("source_not_a_complete_call_block")
    body = text[1:-1].strip()
    calls = []
    while body:
        name = next((n for n in sorted(names, key=len, reverse=True) if body.startswith(n + "(")), None)
        if name is None:
            raise ValueError("source_call_unknown_name_or_syntax")
        begin = len(name)
        depth, quote, escaped, end = 0, None, False, None
        for i in range(begin, len(body)):
            ch = body[i]
            if quote:
                if escaped:
                    escaped = False
                elif ch == "\\":
                    escaped = True
                elif ch == quote:
                    quote = None
                continue
            if ch in {"'", '"'}:
                quote = ch
            elif ch == "(":
                depth += 1
            elif ch == ")":
                depth -= 1
                if depth == 0:
                    end = i
                    break
        if end is None:
            raise ValueError("source_unclosed_call")
        args = {}
        # Publisher parameter names may be Python keywords or contain '-'/'$'.
        # Split only at top level, then parse each VALUE as a literal AST.
        arguments = body[begin + 1:end]
        parts, start, stack, quote, escaped = [], 0, [], None, False
        for pos, ch in enumerate(arguments):
            if quote:
                if escaped:
                    escaped = False
                elif ch == "\\":
                    escaped = True
                elif ch == quote:
                    quote = None
            elif ch in {"'", '"'}:
                quote = ch
            elif ch in "([{":
                stack.append(ch)
            elif ch in ")]}":
                if not stack or stack.pop() != {")": "(", "]": "[", "}": "{"}[ch]:
                    raise ValueError("source_unbalanced_argument")
            elif ch == "," and not stack:
                parts.append(arguments[start:pos])
                start = pos + 1
        if quote or stack:
            raise ValueError("source_unclosed_argument")
        if arguments.strip():
            parts.append(arguments[start:])
        for part in parts:
            key, separator, value = part.strip().partition("=")
            if not separator or not re.fullmatch(r"[\w$][\w$.-]*", key.strip()):
                raise ValueError("source_argument_name_invalid")
            key = key.strip()
            if key in args:
                raise ValueError("source_duplicate_or_unpacking_argument")
            args[key] = literal(ast.parse(value.strip(), mode="eval").body)
        calls.append({"name": name, "arguments": args})
        body = body[end + 1:].strip()
        if body:
            if not body.startswith(","):
                raise ValueError("source_call_separator_invalid")
            body = body[1:].strip()
            if not body:
                raise ValueError("source_call_trailing_comma")
    if not calls:
        raise ValueError("source_empty_call_block")
    return calls


def convert_record(source, record, index):
    issues = []
    try:
        tools = source_tools(source["key"], record)
    except (ValueError, TypeError, KeyError) as exc:
        # Preserve alternate publisher formats rather than guessing their schema.
        tools = None
        issues.append({"turn": None, "reason": str(exc)[:100]})
    names = {t["name"] for t in tools or []}
    messages = record.get("conversations")
    if not isinstance(messages, list) or not messages:
        raise ValueError("source_empty_conversation")
    events = []
    for i, message in enumerate(messages):
        if set(message) != {"from", "value"} or message["from"] not in ROLE_MAP:
            raise ValueError("source_unsupported_message_shape")
        if not isinstance(message["value"], str):
            raise ValueError("source_message_content_not_string")
        event = {"sourceTurn": i, "sourceRole": message["from"],
                 "role": ROLE_MAP[message["from"]], "content": message["value"]}
        if event["role"] == "tool_call":
            try:
                call = json.loads(event["content"])
                if not isinstance(call, dict) or set(call) != {"name", "arguments"}:
                    raise ValueError("source_structured_call_shape")
                if isinstance(call["arguments"], str):
                    call["arguments"] = json.loads(call["arguments"])
                    event["argumentsDecodedFromJsonString"] = True
                if not isinstance(call["name"], str) or not isinstance(call["arguments"], dict):
                    raise ValueError("source_structured_call_invalid_name_or_arguments")
                if call["name"] not in names:
                    event["unresolvedCalls"] = [call]
                    raise ValueError("source_call_not_in_declared_catalog")
                event["calls"] = [call]
            except (ValueError, TypeError) as exc:
                issues.append({"turn": i, "reason": str(exc)[:100]})
        elif event["role"] == "assistant" and source["key"] == "toolace":
            content = event["content"].strip()
            if content.startswith("["):
                try:
                    event["calls"] = parse_toolace_calls(content, names)
                    event["role"] = "tool_call"
                except (ValueError, TypeError, SyntaxError) as exc:
                    issues.append({"turn": i, "reason": type(exc).__name__ + ":call_block_unparsed"})
        if "calls" in event:
            event["selectedToolNames"] = [c["name"] for c in event["calls"]]
            event["unselectedToolApplicability"] = "unknown_not_negative"
        # Ordinary text may be a question, answer, refusal, acknowledgement or plan.
        # There is no source-supplied finish/clarify label; do not fabricate one.
        if event["role"] == "assistant":
            event["finishOrClarifyLabel"] = None
        events.append(event)
    if not any(e["role"] == "user" for e in events):
        raise ValueError("source_no_user_turn")
    source_hash = digest(record)
    return {"id": digest([source["repository"], source["revision"], index, source_hash]),
            "contract": CONTRACT, "source": {"repository": source["repository"],
              "revision": source["revision"], "recordIndex": index, "recordSha256": source_hash,
              "license": source["license"]}, "original": record, "tools": tools, "events": events,
            "parseIssues": issues, "toolCatalogParsed": tools is not None,
            "strictCallParsingPassed": not issues,
            "legacyV2RankingSupervisionAvailable": False, "generatedQuestions": False,
            "generatedTargetLabels": False}


def schema_types(schema):
    if not isinstance(schema, dict):
        return Counter()
    result = Counter()
    if "type" in schema:
        result[str(schema["type"])] += 1
    for v in schema.get("properties", {}).values():
        result.update(schema_types(v))
    items = schema.get("items")
    if isinstance(items, dict):
        result.update(schema_types(items))
    return result


def run(raw, out):
    manifest = verify(raw)
    out.mkdir(parents=True, exist_ok=False)
    reports = []
    artifacts = {}
    for source in manifest["sources"]:
        records = json.loads((raw / "raw" / source["key"] / source["dataFile"]).read_text())
        if not isinstance(records, list):
            raise ValueError("source_dataset_not_list")
        roles, reasons, types = Counter(), Counter(), Counter()
        names, definitions, catalogs, user_texts, conversations = set(), set(), set(), set(), set()
        counters = Counter()
        destination = out / (source["key"] + ".jsonl")
        with destination.open("x") as f, (out / (source["key"] + "-issues.jsonl")).open("x") as bad:
            for i, record in enumerate(records):
                try:
                    row = convert_record(source, record, i)
                except (ValueError, TypeError, KeyError, json.JSONDecodeError) as exc:
                    counters["unconverted"] += 1
                    reason = type(exc).__name__ + ":" + str(exc)[:120]
                    reasons[reason] += 1
                    bad.write(canonical({"index": i, "reason": reason,
                                         "recordSha256": digest(record)}) + "\n")
                    continue
                f.write(canonical(row) + "\n")
                counters["converted"] += 1
                counters["toolCatalogParsedRecords"] += row["toolCatalogParsed"]
                counters["emptyToolCatalogRecords"] += row["tools"] == []
                counters["strictCallParsingPassed"] += row["strictCallParsingPassed"]
                counters["callParsingIssueRecords"] += bool(row["parseIssues"])
                if row["parseIssues"]:
                    bad.write(canonical({"index": i, "issues": row["parseIssues"]}) + "\n")
                roles.update(e["role"] for e in row["events"])
                user_texts.update(e["content"] for e in row["events"] if e["role"] == "user")
                conversation = digest(record["conversations"])
                counters["duplicateConversationRecords"] += conversation in conversations
                conversations.add(conversation)
                if row["tools"] is not None:
                    catalogs.add(digest(sorted(row["tools"], key=lambda t: canonical(t))))
                for tool in row["tools"] or []:
                    names.add(tool["name"])
                    definitions.add(digest(tool))
                    types.update(schema_types(tool["parameters"]))
                for event in row["events"]:
                    counters["parsedFunctionCalls"] += len(event.get("calls", []))
                    counters["virtualThinkCalls"] += sum(c["name"] == "think" for c in event.get("calls", []))
                    counters["callBlocksWithoutImmediateObservation"] += event["role"] == "tool_call" and (
                        event["sourceTurn"] + 1 >= len(row["events"]) or
                        row["events"][event["sourceTurn"] + 1]["role"] != "observation")
        reports.append({"source": source["repository"], "revision": source["revision"],
                        "license": source["license"], "originalRecords": len(records),
                        **dict(counters), "roles": dict(roles), "uniqueUserUtterances": len(user_texts),
                        "uniqueConversationContents": len(conversations), "uniqueToolNames": len(names),
                        "uniqueToolDefinitions": len(definitions), "uniqueToolCatalogs": len(catalogs),
                        "schemaTypeOccurrencesIncludingRepeatedCatalogs": dict(types),
                        "conversionFailures": dict(reasons)})
        print(canonical({"source": source["key"], **dict(counters)}), flush=True)
    for path in out.glob("*.jsonl"):
        with path.open("rb") as f:
            artifacts[path.name] = hashlib.file_digest(f, "sha256").hexdigest()
    result = {"status": "import_and_audit_complete", "contract": CONTRACT,
              "sourceManifestSha256": digest(manifest), "sources": reports,
              "artifactSha256": artifacts, "sourceContentPreserved": True,
              "newConversationsGenerated": 0, "newTargetLabelsGenerated": 0,
              "legacySelfAuthoredDataMixedIn": False, "teacherApiCalled": False,
              "trainingStarted": False, "formalModelsChanged": False,
              "legacyV2TrainerCompatible": False,
              "nextStep": "adapt_supervision_without_inventing_clarify_or_negative_labels",
              "limits": ["public_data_not_assumed_error_free", "no_source_wide_usefulness_or_ranking_gold",
                         "plain_assistant_text_not_split_into_finish_or_clarify",
                         "virtual_think_tools_not_runtime_actions", "no_tool_unseen_split_claim"]}
    write_json(out / "audit.json", result)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--raw", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    run(args.raw, args.out)


if __name__ == "__main__":
    main()
