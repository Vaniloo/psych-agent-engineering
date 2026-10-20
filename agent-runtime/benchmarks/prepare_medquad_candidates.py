"""Audit the original CC BY 4.0 dataset and stage focused historical QA.

Staged answers are NOT current clinical evidence and are NOT imported into the
runtime knowledge index. Source currency and answer-level review remain required.
"""

import argparse
import collections
import json
import re
import subprocess
import xml.etree.ElementTree as ET
from pathlib import Path

DATASET = "https://github.com/abachaa/MedQuAD"
FOCUSES = {
    "anxiety", "anxiety disorders", "attention deficit hyperactivity disorder",
    "autism", "autism spectrum disorder", "bereavement", "bipolar disorder",
    "child mental health", "depression", "eating disorders", "insomnia",
    "mental disorders", "mental health", "obsessive compulsive disorder", "panic disorder",
    "phobias", "post traumatic stress disorder", "postpartum depression", "schizophrenia",
    "sleep deprivation and deficiency", "sleep disorders", "suicide", "teen mental health",
}


def in_scope(focus):
    # Dataset topic labels, not arbitrary occurrences of "mental" in rare diseases.
    return re.sub(r"[^a-z0-9]+", " ", focus.casefold()).strip() in FOCUSES


def prepare(repository, output):
    repository, output = Path(repository).resolve(), Path(output)
    output.mkdir(parents=True, exist_ok=False)
    revision = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repository, text=True).strip()
    counts, folders, types = collections.Counter(), collections.Counter(), collections.Counter()
    candidates = []
    for path in sorted(repository.glob("*/*.xml")):
        raw = path.read_bytes(); counts["xmlFiles"] += 1
        if b"<!DOCTYPE" in raw or b"<!ENTITY" in raw:
            counts["unsafeXmlRejected"] += 1
            continue
        try:
            document = ET.fromstring(raw)
        except ET.ParseError:
            counts["malformedXmlRejected"] += 1
            continue
        focus = document.findtext("Focus") or ""
        for pair in document.findall(".//QAPair"):
            counts["observedQaPairs"] += 1
            question = pair.find("Question"); answer = pair.findtext("Answer") or ""
            if answer.strip():
                counts["observedAnsweredPairs"] += 1
            if not in_scope(focus):
                continue
            counts["focusedPairsIncludingMissingAnswers"] += 1
            if not answer.strip() or question is None or not (question.text or "").strip():
                counts["focusedMissingAnswerOrQuestion"] += 1
                continue
            counts["focusedAnsweredCandidates"] += 1
            relative = path.relative_to(repository).as_posix()
            folders[path.parent.name] += 1; types[question.get("qtype", "unknown")] += 1
            candidates.append({"id": relative + ":" + question.get("qid", pair.get("pid", "unknown")),
                "focus": focus, "question": question.text, "answer": answer,
                "questionType": question.get("qtype", "unknown"), "originalSource": document.get("url"),
                "datasetSource": DATASET, "datasetRevision": revision,
                "recordSource": DATASET + "/blob/" + revision + "/" + relative,
                "license": "CC-BY-4.0", "historical": True, "currentSourceReviewed": False,
                "runtimeEligible": False})
    (output / "candidates.jsonl").write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in candidates))
    (output / "LICENSE.txt").write_text((repository / "LICENSE.txt").read_text())
    report = {"dataset": DATASET, "revision": revision, "license": "CC-BY-4.0",
              "licenseSource": DATASET + "/blob/" + revision + "/LICENSE.txt",
              "citation": "Asma Ben Abacha and Dina Demner-Fushman. A Question-Entailment Approach to Question Answering. BMC Bioinformatics, 2019.",
              "authorsAdvertisedPairs": 47457, "observed": dict(counts), "byFolder": dict(folders),
              "questionTypes": dict(types), "focuses": sorted({c["focus"] for c in candidates}),
              "runtimeImport": False, "reviewStatus": "historical_candidates_require_current_source_and_answer_review",
              "note": "Authors removed answers in three MedlinePlus subsets; no attempt to reconstruct them. Whole-dataset advertised and observed pair counts differ; retain both counts."}
    (output / "audit.json").write_text(json.dumps(report, ensure_ascii=False, indent=2))
    print(json.dumps(report, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository", required=True); parser.add_argument("--output", required=True)
    args = parser.parse_args(); prepare(args.repository, args.output)
