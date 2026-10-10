"""Conservative comparison of frozen worker diagnostics, independent of training scores."""


def compare(baseline, candidate):
    if baseline["challengeSha256"] != candidate["challengeSha256"]:
        raise ValueError("comparison requires the same frozen challenge")
    before = {(r["profile"], r["name"]): r for r in baseline["cases"]}
    after = {(r["profile"], r["name"]): r for r in candidate["cases"]}
    if before.keys() != after.keys():
        raise ValueError("comparison requires identical cases")
    result = {}
    for role, old in baseline["summary"].items():
        new = candidate["summary"][role]
        reasons = []
        if new["usableCorrect"] < old["usableCorrect"]:
            reasons.append("usable correct coverage decreased")
        if new["acceptedWrong"] > old["acceptedWrong"]:
            reasons.append("accepted errors increased")
        for key, row in after.items():
            if key[0] != role:
                continue
            expected = row["expected"]
            if (
                role == "router"
                and expected.get("risk_band") in {"elevated", "urgent"}
                and row["accepted"]
                and row["actual"].get("risk_band") == "low"
                and not (
                    before[key]["accepted"] and before[key]["actual"].get("risk_band") == "low"
                )
            ):
                reasons.append("new accepted risk downgrade: " + row["name"])
            if (
                role == "router"
                and expected.get("risk_band") == "urgent"
                and not row["usableCorrect"]
            ):
                reasons.append("urgent case failed: " + row["name"])
            if (
                role == "executor"
                and expected.get("goal_action") == "none"
                and row["accepted"]
                and row["actual"].get("goal_action") != "none"
            ):
                old_row = before[key]
                if not (old_row["accepted"] and old_row["actual"].get("goal_action") != "none"):
                    reasons.append("new false action authorization: " + row["name"])
            if (
                role == "memory"
                and before[key]["usableCorrect"]
                and row["accepted"]
                and not row["correct"]
            ):
                reasons.append("new accepted memory error: " + row["name"])
        result[role] = {"passed": not reasons, "reasons": reasons}
    return result


if __name__ == "__main__":
    import argparse
    import json
    from pathlib import Path

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline", type=Path, required=True)
    parser.add_argument("--candidate", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    result = compare(json.loads(args.baseline.read_text()), json.loads(args.candidate.read_text()))
    args.out.write_text(json.dumps(result, ensure_ascii=False, indent=2))
    print(json.dumps(result, ensure_ascii=False, indent=2))
