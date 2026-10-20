"""Verify full source fidelity and accounting after conversion; no quality-gold claim."""

import argparse
import hashlib
import json
from collections import Counter
from pathlib import Path

from .convert import CONTRACT, digest
from .fetch import verify, write_json


def check(raw, converted):
    manifest = verify(raw)
    audit = json.loads((converted / "audit.json").read_text())
    if audit["contract"] != CONTRACT or audit["sourceManifestSha256"] != digest(manifest):
        raise ValueError("interchange_source_or_contract_mismatch")
    expected_files = {s["key"] + suffix for s in manifest["sources"]
                      for suffix in (".jsonl", "-issues.jsonl")}
    if set(audit["artifactSha256"]) != expected_files:
        raise ValueError("interchange_file_list_mismatch")
    for name, expected in audit["artifactSha256"].items():
        with (converted / name).open("rb") as f:
            if hashlib.file_digest(f, "sha256").hexdigest() != expected:
                raise ValueError("interchange_file_checksum_mismatch")
    results = []
    for source in manifest["sources"]:
        original = json.loads((raw / "raw" / source["key"] / source["dataFile"]).read_text())
        seen, reasons, strict = set(), Counter(), 0
        with (converted / (source["key"] + ".jsonl")).open() as f:
            for line in f:
                row = json.loads(line)
                index = row["source"]["recordIndex"]
                if type(index) is not int or not 0 <= index < len(original) or index in seen:
                    raise ValueError("source_index_out_of_range_or_duplicate")
                seen.add(index)
                expected_source = {"repository": source["repository"], "revision": source["revision"],
                                   "recordIndex": index, "recordSha256": digest(original[index]),
                                   "license": source["license"]}
                if row["source"] != expected_source or row["original"] != original[index]:
                    raise ValueError("original_record_not_preserved")
                if ([e["content"] for e in row["events"]] !=
                        [e["value"] for e in original[index]["conversations"]]):
                    raise ValueError("original_message_not_preserved")
                strict += row["strictCallParsingPassed"]
                reasons.update(i["reason"] for i in row["parseIssues"])
        if len(seen) != len(original):
            raise ValueError("source_records_missing_from_interchange")
        results.append({"source": source["key"], "recordsVerified": len(seen),
                        "strictSyntaxRecords": strict, "issueOccurrences": dict(reasons)})
    report = {"status": "passed", "sources": results, "trainingReadinessClaimed": False,
              "sourceManifestSha256": digest(manifest), "auditSha256": digest(audit),
              "checks": ["raw_and_interchange_hashes", "all_original_records_equal",
                         "all_source_metadata", "all_event_contents_equal", "all_source_indices"]}
    write_json(converted / "verification.json", report)
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--raw", type=Path, required=True)
    parser.add_argument("--converted", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(check(args.raw, args.converted)))
