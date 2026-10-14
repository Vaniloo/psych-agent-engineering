"""Frozen project-policy diagnostics of deployed workers and Python guard seams.

No training, generation API, business writes, or threshold changes. These authored
cases are not independent human or clinical labels. Each role runs serially;
roles may run concurrently. Timings therefore are not a load-test benchmark.
"""

import argparse
import asyncio
import hashlib
import json
import os
import time
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

import httpx

from psych_agent_runtime.decision_contract import (
    ROUTER_QUESTIONS,
    memory_evidence_state,
    memory_pair_state,
    profile_contract,
)
from psych_agent_runtime.decision_router import LocalDecisions
from psych_agent_runtime.goal_creation import allow_creation
from psych_agent_runtime.goal_progress import eligible_report, select_progress
from psych_agent_runtime.planner import AgentPlanner
from psych_agent_runtime.route_policy import plan_for_route
from psych_agent_runtime.safety import SafetyScanner

from .readiness_challenge import MODELS


def fingerprint(role, state):
    if role == "memory" and "candidate" in state:
        state = memory_pair_state(state)
    elif role == "memory" and "evidence" in state:
        state = memory_evidence_state(state)
    return (
        role
        + ":"
        + hashlib.sha256(json.dumps(state, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    )


def isolate(rows, datasets):
    """Exclude exact inputs, including memory metadata and legacy batch projection.

    This is deliberately an exact audit; paraphrases and policy-template overlap
    need independent review and must not be described as proved absent.
    """
    known = set()
    files = []
    skipped_legacy = 0
    for name in (
        "three-laya-domain-v2",
        "three-laya-public-r6",
        "pair-round7-final",
        "sufficiency-round8",
        "guard-refinement-round9",
        "semantic-contrast-round10",
        "task-optimization-round11",
    ):
        for role in MODELS:
            for split in ("train", "calibration", "validation", "test"):
                path = datasets / name / role / (split + ".jsonl")
                if not path.exists():
                    continue
                files.append(str(path.relative_to(datasets)))
                for line in path.open():
                    row = json.loads(line)
                    state = row["state"]
                    states = [state]
                    if role == "memory" and "candidates" in state:
                        states.extend(
                            {
                                "query": state["query"],
                                "candidate": {
                                    "role": c.get("role")
                                    or (
                                        "assistant"
                                        if c["content"].startswith("助手建议：")
                                        else "user"
                                    ),
                                    "content": c["content"],
                                },
                            }
                            for c in state["candidates"]
                        )
                    for item in states:
                        try:
                            known.add(fingerprint(role, item))
                        except (TypeError, ValueError):
                            # Legacy evidence fixtures without explicit roles cannot
                            # be equated to today's source-aware serving contract.
                            skipped_legacy += 1
    included, excluded = [], []
    for row in rows:
        (excluded if fingerprint(row["profile"], row["state"]) in known else included).append(row)
    return included, {
        "checkedFiles": files,
        "knownCanonicalInputs": len(known),
        "legacyInputsNotComparable": skipped_legacy,
        "excludedNames": [r["name"] for r in excluded],
        "scope": "Exact state equality only; does not establish semantic independence.",
    }


def matches(choice, expected):
    return choice in expected if isinstance(expected, list) else choice == expected


def summary(rows):
    valid = [r for r in rows if not r.get("errorType")]
    latencies = sorted(r["latencyMs"] for r in valid)
    return {
        "rows": len(rows),
        "families": len({r["family"] for r in rows}),
        "rawCorrect": sum(r["correct"] for r in valid),
        "usableCorrect": sum(r["usableCorrect"] for r in valid),
        "acceptedWrong": sum(r["accepted"] and not r["correct"] for r in valid),
        "abstained": sum(not r["accepted"] for r in valid),
        "apiErrors": len(rows) - len(valid),
        "criticalAcceptedWrong": dict(
            Counter(
                r["critical"] for r in valid if r["critical"] and r["accepted"] and not r["correct"]
            )
        ),
        "applicationChecks": sum(bool(r.get("application")) for r in valid),
        "applicationMismatches": sum(
            bool(r.get("application")) and not r["application"]["correct"] for r in valid
        ),
        "p50LatencyMs": latencies[len(latencies) // 2] if latencies else None,
        "p95LatencyMs": latencies[min(len(latencies) - 1, int(len(latencies) * 0.95))]
        if latencies
        else None,
    }


class RecordedModel:
    def __init__(self, decisions):
        self.decisions = decisions

    async def evaluate(self, *args, **kwargs):
        return {"decisions": self.decisions}


async def application_probe(row, decisions):
    """Use application Python logic with recorded worker answers; no DB mutation.

    Goal checks stop at Python candidates. Java final validation, outbox delivery,
    context loading and generated replies are outside this test.
    """
    want = row["applicationExpected"]
    if not want:
        return {}
    state = row["state"]
    if row["profile"] == "router":
        from psych_agent_runtime.graph import AgentGraphRuntime

        text = state["message"]
        route, kind, risk, support = (
            decisions[t] for t in ("route", "conversation_kind", "risk_band", "support_need")
        )
        routing = {
            "kindAccepted": kind["accepted"],
            "conversationKind": kind["choice"],
            "riskDecision": {
                "riskBand": risk["choice"],
                "accepted": risk["accepted"]
                and (support["accepted"] or risk["choice"] == "urgent"),
                "needsProfessionalSupport": support["choice"] == "professional",
            },
        }
        plan = (
            plan_for_route(route["choice"], text, "read-only diagnostic", routing)
            if route["accepted"] and kind["accepted"]
            else AgentPlanner._deterministic_plan(text)
        )
        plan.routing = routing
        scan = SafetyScanner().scan(text)
        if scan.high_risk:
            level = "crisis"
        else:
            runtime = AgentGraphRuntime.__new__(AgentGraphRuntime)
            runtime._emit = lambda *args: None
            outcome = await runtime._risk_decision(
                {
                    "message": text,
                    "plan": plan.model_dump(),
                    "context": {
                        "username": "synthetic-readiness",
                        "sessionId": 777,
                        "messages": state.get("history", []),
                        "sessionSummary": state.get("session_summary", ""),
                    },
                }
            )
            level = outcome["support_level"]
        correct = (
            matches(level, want["support_level"]) if "support_level" in want else level != "crisis"
        )
        return {"correct": correct, "supportLevel": level, "scannerTriggered": scan.high_risk}
    model = RecordedModel(decisions)
    text = state["userExpression"]
    if "createCandidate" in want:
        actual = await allow_creation(text, model)
        return {"correct": actual == want["createCandidate"], "createCandidate": actual}
    goals = [
        {
            "id": c["goalId"],
            "version": c["version"],
            "title": c["goalTitle"],
            "status": "active",
            "actions": [{"id": c["actionId"], "title": c["actionTitle"], "status": "planned"}],
        }
        for c in state["candidates"]
    ]
    eligible = eligible_report(text)
    selected = await select_progress(text, goals, model) if eligible else None
    correct = bool(selected) == want["writeCandidate"]
    if selected and want["writeCandidate"]:
        expected_action = row["expected"]["goal_action"]
        expected_item = state["candidates"][int(expected_action.removeprefix("action_"))]
        correct = (
            selected["goalId"] == expected_item["goalId"]
            and selected["actionId"] == expected_item["actionId"]
            and matches(selected["outcome"], row["expected"]["goal_outcome"])
        )
    return {
        "correct": correct,
        "writeCandidate": bool(selected),
        "eligibleReport": eligible,
        "selection": selected,
    }


async def evaluate_role(role, rows, expected_model=None):
    env = role.upper()
    url = os.environ[env + "_BASE_URL"]
    key = os.environ[env + "_API_KEY"]
    minimum = float(os.environ.get(env + "_MIN_PROBABILITY", ".85"))
    async with httpx.AsyncClient(base_url=url, timeout=30, trust_env=False) as health_client:
        response = await health_client.get("/health")
        response.raise_for_status()
        before = response.json()
        if not before["ready"] or before["model"] != (expected_model or MODELS[role]):
            raise ValueError("unexpected deployed model")
        _, questions = profile_contract(role, before["contractVersion"])
        model = LocalDecisions(url, key, 30, before["contractVersion"], questions, minimum)
        results = []
        try:
            for i, case in enumerate(rows):
                tasks = list(ROUTER_QUESTIONS) if role == "router" else list(case["expected"])
                # Selection requires both decisions even when only identity is
                # labelled. Score only the frozen expected tasks.
                if role == "executor" and "goal_action" in tasks and "goal_outcome" not in tasks:
                    tasks.append("goal_outcome")
                labels = None
                if role == "executor" and "candidates" in case["state"]:
                    labels = {
                        "goal_action": ["none"]
                        + [f"action_{n}" for n in range(len(case["state"]["candidates"]))]
                    }
                elif role == "executor" and "allowedTools" in case["state"]:
                    labels = {"next_action": case["state"]["allowedTools"] + ["finish", "clarify"]}
                start = time.monotonic()
                item = {**case}
                try:
                    result = await model.evaluate(case["state"], tasks, labels_by_task=labels)
                    if result["model"] != before["model"]:
                        raise ValueError("model version changed during evaluation")
                    decisions = result["decisions"]
                    correct = all(
                        matches(decisions[t]["choice"], want)
                        for t, want in case["expected"].items()
                    )
                    accepted = all(decisions[t]["accepted"] for t in case["expected"])
                    item.update(
                        decisions=decisions,
                        correct=correct,
                        accepted=accepted,
                        usableCorrect=correct and accepted,
                    )
                    try:
                        item["application"] = await application_probe(case, decisions)
                    except (ValueError, KeyError, TypeError) as exc:
                        # An evaluator seam error must not be reported as an API
                        # failure or erase the recorded model decision.
                        item["application"] = {
                            "correct": False,
                            "probeErrorType": type(exc).__name__,
                        }
                except (httpx.HTTPError, ValueError, KeyError, TypeError) as exc:
                    item.update(
                        errorType=type(exc).__name__,
                        correct=False,
                        accepted=False,
                        usableCorrect=False,
                    )
                item["latencyMs"] = round(1000 * (time.monotonic() - start), 2)
                results.append(item)
                if (i + 1) % 25 == 0:
                    print(
                        json.dumps({"profile": role, "completed": i + 1, "total": len(rows)}),
                        flush=True,
                    )
        finally:
            await model.close()
        response = await health_client.get("/health")
        response.raise_for_status()
        after = response.json()
        if before != after:
            raise ValueError("worker health/artifact changed during evaluation")
    print(json.dumps({"profile": role, "summary": summary(results)}), flush=True)
    return {"role": role, "health": before, "cases": results, "clientMinimumProbability": minimum}


async def run(data, out, datasets, isolation_audit=None, expected_models=None):
    if out.exists():
        raise ValueError("refuse to overwrite evaluation")
    raw = data.read_bytes()
    manifest = json.loads(data.with_name("manifest.json").read_text())
    digest = hashlib.sha256(raw).hexdigest()
    if digest != manifest["challengeSha256"]:
        raise ValueError("frozen challenge hash mismatch")
    rows = [json.loads(line) for line in raw.decode().splitlines() if line]
    if isolation_audit:
        audit = json.loads(isolation_audit.read_text())
        if audit.get("challengeSha256") != digest:
            raise ValueError("isolation audit challenge hash mismatch")
        rows = [r for r in rows if r["name"] not in set(audit["excludedNames"])]
    else:
        rows, audit = isolate(rows, datasets)
    out.mkdir(parents=True)
    (out / "isolation-audit.json").write_text(json.dumps(audit, ensure_ascii=False, indent=2))
    results = await asyncio.gather(
        *[
            evaluate_role(
                role,
                [r for r in rows if r["profile"] == role],
                (expected_models or manifest["expectedModels"])[role],
            )
            for role in MODELS
            if any(r["profile"] == role for r in rows)
        ]
    )
    report = {
        "finishedAt": datetime.now(UTC).isoformat(),
        "challengeSha256": digest,
        "manifest": manifest,
        "isolationAudit": audit,
        "scope": "Authored policy diagnostics, not independent human labels or clinical validation. Application checks are Python seams only; no generation, DB writes or load testing.",
        "roles": {},
    }
    for r in results:
        cases = r["cases"]
        tasks = {}
        for task in sorted({t for c in cases for t in c["expected"]}):
            relevant = [c for c in cases if task in c["expected"] and not c.get("errorType")]
            decisions = [(c["decisions"][task], c["expected"][task]) for c in relevant]
            tasks[task] = {
                "decisions": len(decisions),
                "usableCorrect": sum(
                    d["accepted"] and matches(d["choice"], w) for d, w in decisions
                ),
                "acceptedWrong": sum(
                    d["accepted"] and not matches(d["choice"], w) for d, w in decisions
                ),
                "abstained": sum(not d["accepted"] for d, _ in decisions),
            }
        report["roles"][r["role"]] = {
            **r,
            "all": summary(cases),
            "primary": summary([c for c in cases if c["variant"] == "primary"]),
            "categories": {
                cat: summary([c for c in cases if c["category"] == cat])
                for cat in sorted({c["category"] for c in cases})
            },
            "tasks": tasks,
        }
    (out / "results.json").write_text(json.dumps(report, ensure_ascii=False, indent=2))
    compact = {
        **report,
        "roles": {
            role: {k: v for k, v in r.items() if k != "cases"}
            for role, r in report["roles"].items()
        },
    }
    (out / "summary.json").write_text(json.dumps(compact, ensure_ascii=False, indent=2))
    return compact


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--datasets", type=Path)
    parser.add_argument("--isolation-audit", type=Path)
    parser.add_argument(
        "--expected-models", type=Path, help="Explicit candidate identity; labels remain frozen"
    )
    args = parser.parse_args()
    if not args.datasets and not args.isolation_audit:
        parser.error("--datasets or --isolation-audit required")
    asyncio.run(
        run(
            args.data,
            args.out,
            args.datasets,
            args.isolation_audit,
            json.loads(args.expected_models.read_text()) if args.expected_models else None,
        )
    )
