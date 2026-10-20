"""Source-grounded next-action demonstrations. No synthetic questions or utility gold."""

import argparse
import json
import unicodedata
from collections import Counter, defaultdict
from pathlib import Path

from psych_agent_runtime.public_tool_contract import CONTRACT, TEXT

from .convert import canonical, digest
from .fetch import SOURCES, write_json
from .verify_interchange import check

SPLITS = ("train", "validation", "test")


def normalized(value):
    return " ".join(unicodedata.normalize("NFKC", value).casefold().split())


def keys(row):
    first = next(e["content"] for e in row["events"] if e["role"] == "user")
    return (["tool:" + normalized(t["name"]) for t in row["tools"]]
            + ["first_query:" + digest(normalized(first)),
               "conversation:" + digest(row["original"]["conversations"])])


def assign_components(rows, seed):
    """Any shared tool NAME, initial user query or conversation stays in one split."""
    parent = {}

    def find(x):
        parent.setdefault(x, x)
        root = x
        while parent[root] != root:
            root = parent[root]
        while parent[x] != x:
            previous = x
            x = parent[x]
            parent[previous] = root
        return root

    for row in rows:
        row_keys = keys(row)
        for key in row_keys[1:]:
            a, b = find(row_keys[0]), find(key)
            if a != b:
                parent[max(a, b)] = min(a, b)
    groups = defaultdict(list)
    for row in rows:
        groups[find(keys(row)[0])].append(row)
    components = sorted(groups.items(), key=lambda item: (-len(item[1]), digest([seed, item[0]])))
    target = {s: len(rows) * ratio for s, ratio in zip(SPLITS, (0.8, 0.1, 0.1), strict=True)}
    counts, assignments = Counter(), {}
    for root, members in components:
        split = max(SPLITS, key=lambda s: (target[s] - counts[s]) / target[s])
        counts[split] += len(members)
        for row in members:
            assignments[row["id"]] = (split, digest(root))
    return assignments, {"components": len(groups), "largestComponentRecords": max(map(len, groups.values())),
                         "recordsBySplit": dict(counts), "seed": seed,
                         "isolatedKeys": ["normalized_tool_name", "normalized_initial_user_query", "conversation_hash"],
                         "claim": "exact_key_disjoint_not_semantic_or_tool_family_disjoint"}


def decisions(record):
    """Targets are observed source roles/calls, never an inferred finish/clarify taxonomy."""
    if not record["strictCallParsingPassed"] or record["tools"] is None:
        raise ValueError("source_record_not_strictly_parsed")
    if any(t["name"] == "think" for t in record["tools"]):
        raise ValueError("virtual_reasoning_catalog_not_supported")
    candidates = [{"kind": "tool", "tool": t} for t in record["tools"]] + [TEXT]
    indices = {t["name"]: i for i, t in enumerate(record["tools"])}
    history = []
    out = []
    for event in record["events"]:
        if event["role"] in {"assistant", "tool_call"}:
            if not any(e["role"] == "user" for e in history):
                raise ValueError("source_action_before_user")
            chosen = (sorted({indices[c["name"]] for c in event["calls"]})
                      if event["role"] == "tool_call" else [len(candidates) - 1])
            # The target event and arguments are NOT part of the input context.
            out.append({"id": digest([record["id"], event["sourceTurn"], CONTRACT]), "contract": CONTRACT,
                        "episodeId": record["id"], "source": record["source"],
                        "sourceTurn": event["sourceTurn"], "targetContentSha256": digest(event["content"]),
                        "context": list(history), "candidateTools": candidates, "targetIndices": chosen,
                        "observedAction": "tool_call" if event["role"] == "tool_call" else "assistant_text",
                        "supervision": "observed_next_action_only", "absoluteApplicabilityLabels": None,
                        "finishOrClarifyLabel": None, "informativeChoice": len(candidates) > 1})
        history.append({"role": event["role"], "content": event["content"], "sourceTurn": event["sourceTurn"]})
    return out


def build(raw, converted, out, source_key="toolace", seed=191019):
    if out.exists():
        raise ValueError("refuse_overwriting_course")
    verification = check(raw, converted)
    source = next(s for s in SOURCES if s["key"] == source_key)
    rows, exclusions, seen = [], [], set()
    for line in (converted / (source_key + ".jsonl")).open():
        row = json.loads(line)
        reason = None
        identity = digest([row["tools"], row["original"]["conversations"]])
        if not row["strictCallParsingPassed"]:
            reason = "source_parse_issues"
        elif identity in seen:
            reason = "duplicate_catalog_and_conversation"
        else:
            try:
                if not decisions(row):
                    reason = "no_observed_assistant_action"
            except ValueError as exc:
                reason = str(exc)
        if reason:
            exclusions.append({"episodeId": row["id"], "source": row["source"], "reason": reason})
        else:
            seen.add(identity)
            rows.append(row)
    if not rows:
        raise ValueError("empty_public_course")
    assignments, grouping = assign_components(rows, seed)
    out.mkdir(parents=True)
    counts, informative, types = Counter(), Counter(), Counter()
    files = {s: (out / (s + ".jsonl")).open("x") for s in SPLITS}
    try:
        for row in rows:
            split, component = assignments[row["id"]]
            for decision in decisions(row):
                decision.update(split=split, componentId=component)
                files[split].write(canonical(decision) + "\n")
                counts[split] += 1
                informative[split] += decision["informativeChoice"]
                types[(split, decision["observedAction"])] += 1
    finally:
        for f in files.values():
            f.close()
    (out / "excluded.jsonl").write_text("".join(canonical(e) + "\n" for e in exclusions))
    artifacts = {}
    import hashlib
    for p in out.glob("*.jsonl"):
        with p.open("rb") as f:
            artifacts[p.name] = hashlib.file_digest(f, "sha256").hexdigest()
    manifest = {"status": "course_built", "contract": CONTRACT, "source": source,
                "sourceVerificationSha256": digest(verification), "episodes": len(rows),
                "excludedEpisodes": len(exclusions), "exclusionReasons": dict(Counter(e["reason"] for e in exclusions)),
                "rowsBySplit": dict(counts), "informativeRowsBySplit": dict(informative),
                "observedActionsBySplit": {s: {a: types[(s, a)] for a in ("tool_call", "assistant_text")} for s in SPLITS},
                "grouping": grouping, "artifactSha256": artifacts,
                "sourcePolicy": "primary_apache" if source_key == "toolace" else "noncommercial_research_only",
                "newQuestionsGenerated": 0, "semanticGoldInferred": False, "syntheticLegacyMixed": False,
                "trainingStarted": False, "deploymentEnabled": False,
                "limits": ["demonstration_not_exhaustive_utility_gold", "assistant_text_is_not_task_finished",
                           "no_argument_generation_training", "no_absolute_confidence_calibration",
                           "no_semantic_family_disjoint_claim", "token_budget_not_checked_yet"]}
    write_json(out / "manifest.json", manifest)
    load(out)
    return manifest


def load(folder):
    import hashlib
    manifest = json.loads((folder / "manifest.json").read_text())
    if manifest["contract"] != CONTRACT or manifest["status"] != "course_built":
        raise ValueError("public_course_contract_mismatch")
    source = next((s for s in SOURCES if s == manifest["source"]), None)
    if source is None:
        raise ValueError("unapproved_public_course_source")
    if set(manifest["artifactSha256"]) != {s + ".jsonl" for s in SPLITS} | {"excluded.jsonl"}:
        raise ValueError("public_course_file_list_mismatch")
    for name, sha in manifest["artifactSha256"].items():
        with (folder / name).open("rb") as f:
            if hashlib.file_digest(f, "sha256").hexdigest() != sha:
                raise ValueError("public_course_checksum_mismatch")
    parts, owners, ids = {}, {}, set()
    for split in SPLITS:
        parts[split] = []
        with (folder / (split + ".jsonl")).open() as f:
            for line in f:
                row = json.loads(line)
                if row["id"] in ids or row["split"] != split or row["contract"] != CONTRACT:
                    raise ValueError("duplicate_id_or_split_contract_mismatch")
                ids.add(row["id"])
                if (row["source"]["repository"], row["source"]["revision"], row["source"]["license"]) != (
                        source["repository"], source["revision"], source["license"]):
                    raise ValueError("mixed_or_unapproved_course_source")
                if row["absoluteApplicabilityLabels"] is not None or row["finishOrClarifyLabel"] is not None:
                    raise ValueError("unsupported_semantic_labels")
                n = len(row["candidateTools"])
                if not row["targetIndices"] or any(type(i) is not int or not 0 <= i < n for i in row["targetIndices"]):
                    raise ValueError("invalid_observed_target")
                if any(e["sourceTurn"] >= row["sourceTurn"] for e in row["context"]):
                    raise ValueError("target_or_future_event_in_context")
                first = next(e["content"] for e in row["context"] if e["role"] == "user")
                isolation = ["episode:" + row["episodeId"], "component:" + row["componentId"],
                             "first_query:" + digest(normalized(first))]
                isolation.extend("tool:" + normalized(c["tool"]["name"]) for c in row["candidateTools"] if c["kind"] == "tool")
                for key in isolation:
                    if owners.setdefault(key, split) != split:
                        raise ValueError("public_course_split_leakage")
                parts[split].append(row)
        if len(parts[split]) != manifest["rowsBySplit"][split] or not parts[split]:
            raise ValueError("public_course_count_mismatch_or_empty_split")
    return manifest, parts


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--raw", type=Path, required=True)
    p.add_argument("--converted", type=Path, required=True)
    p.add_argument("--out", type=Path, required=True)
    p.add_argument("--source", choices=[s["key"] for s in SOURCES], default="toolace")
    p.add_argument("--seed", type=int, default=191019)
    a = p.parse_args()
    print(json.dumps(build(a.raw, a.converted, a.out, a.source, a.seed)))
