"""Apply a recorded release policy to frozen per-case baseline/candidate results."""

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path

from .evaluate_readiness import matches, summary


def review_role(baseline, candidate):
    before = {r["name"]: r for r in baseline["cases"]}
    after = {r["name"]: r for r in candidate["cases"]}
    if len(before) != len(baseline["cases"]) or len(after) != len(candidate["cases"]):
        raise ValueError("duplicate case name")
    if before.keys() != after.keys():
        raise ValueError("baseline/candidate case sets differ")
    new_critical, critical_errors, false_writes, lost, groups = [], [], [], [], {}
    missing_application = []
    for name, row in after.items():
        old = before[name]
        for field in ("state", "expected", "applicationExpected", "evaluationGroup", "critical"):
            if old.get(field) != row.get(field):
                raise ValueError("baseline/candidate frozen labels or inputs differ")
        if row.get("errorType") or old.get("errorType"):
            continue
        if old["usableCorrect"] and not row["usableCorrect"]:
            lost.append(name)
        if row["critical"]:
            for task, label in row["expected"].items():
                a, b = row["decisions"][task], old["decisions"][task]
                if a["accepted"] and not matches(a["choice"], label):
                    critical_errors.append(
                        {"name": name, "task": task, "expected": label, "candidate": a["choice"]}
                    )
                if (
                    a["accepted"]
                    and not matches(a["choice"], label)
                    and not (b["accepted"] and not matches(b["choice"], label))
                ):
                    new_critical.append(
                        {
                            "name": name,
                            "task": task,
                            "expected": label,
                            "before": b["choice"],
                            "beforeAccepted": b["accepted"],
                            "candidate": a["choice"],
                        }
                    )
        want, app = row["applicationExpected"], row.get("application", {})
        if want and (not app or "correct" not in app or app.get("probeErrorType")):
            missing_application.append(name)
        if (want.get("writeCandidate") is False and app.get("writeCandidate")) or (
            want.get("createCandidate") is False and app.get("createCandidate")
        ):
            false_writes.append(name)
    for group in sorted({r["evaluationGroup"] for r in after.values()}):
        groups[group] = {
            "before": summary([r for r in before.values() if r["evaluationGroup"] == group]),
            "candidate": summary([r for r in after.values() if r["evaluationGroup"] == group]),
        }
    old_total, new_total = summary(baseline["cases"]), summary(candidate["cases"])
    checks = {
        "allTasksEnabled": all(candidate["health"]["tasks"].values()),
        "noApiErrors": old_total["apiErrors"] == new_total["apiErrors"] == 0,
        "noGroupCoverageRegression": all(
            g["candidate"]["usableCorrect"] >= g["before"]["usableCorrect"] for g in groups.values()
        ),
        "strictCoverageImprovement": new_total["usableCorrect"] > old_total["usableCorrect"],
        "noNewCriticalAcceptedErrors": not new_critical,
        "noCriticalAcceptedErrors": not critical_errors,
        "noFalseAutomaticGoalWrites": not false_writes,
        "applicationProbesComplete": not missing_application,
    }
    return {
        "passed": all(checks.values()),
        "checks": checks,
        "groups": groups,
        "baseline": old_total,
        "candidate": new_total,
        "newCriticalErrors": new_critical,
        "criticalAcceptedErrors": critical_errors,
        "assessmentScope": "Development candidate gate, not independent public-launch validation.",
        "falseAutomaticWrites": false_writes,
        "missingApplicationChecks": missing_application,
        "lostPreviouslyUsableCases": lost,
        "previousModel": baseline["health"]["model"],
        "candidateModel": candidate["health"]["model"],
    }


def review(baseline, candidate, policy, audit):
    if (
        baseline["challengeSha256"] != candidate["challengeSha256"]
        or audit["challengeSha256"] != candidate["challengeSha256"]
    ):
        raise ValueError("frozen challenge hash differs")
    isolated = not audit["excludedNames"]
    roles = {}
    for role, model in policy["candidateModels"].items():
        if (
            candidate["roles"][role]["health"]["model"] != model
            or baseline["roles"][role]["health"]["model"] != policy["baselineModels"][role]
        ):
            raise ValueError("unexpected model identity")
        result = review_role(baseline["roles"][role], candidate["roles"][role])
        result["checks"]["exactIsolation"] = isolated
        result["passed"] = all(result["checks"].values())
        roles[role] = result
    return {
        "reviewedAt": datetime.now(UTC).isoformat(),
        "scope": policy["scope"],
        "challengeSha256": candidate["challengeSha256"],
        "policy": policy,
        "isolationAudit": audit,
        "roles": roles,
    }


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    for name in ("baseline", "candidate", "policy", "audit", "out"):
        p.add_argument("--" + name, type=Path, required=True)
    a = p.parse_args()
    result = review(
        *[json.loads(path.read_text()) for path in [a.baseline, a.candidate, a.policy, a.audit]]
    )
    a.out.write_text(json.dumps(result, ensure_ascii=False, indent=2))
    print(
        json.dumps(
            {
                role: {
                    "passed": r["passed"],
                    "checks": r["checks"],
                    "newCriticalErrors": r["newCriticalErrors"],
                }
                for role, r in result["roles"].items()
            },
            ensure_ascii=False,
            indent=2,
        )
    )
