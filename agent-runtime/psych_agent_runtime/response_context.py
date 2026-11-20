"""Keep tool facts in valid bounded JSON, rather than cutting a JSON string."""

import copy
import json

from .report_context import dashboard_with_availability


def _project(name, value):
    if not isinstance(value, dict) or value.get("unavailable"):
        return value
    if name == "search_role_voice":
        # Role references affect voice only, never answer an unrelated factual question.
        return {"roleReferenceSearchDone": True}
    if name == "search_web":
        if 'pipeline' not in value:
            return value
        return {k: v for k, v in {
            'available': value.get('available'), 'query': value.get('query'),
            'retrievedAt': value.get('retrievedAt'), 'evidenceKind': value.get('evidenceKind'),
            'coverageLimited': True,
            'passages': [{k: p[k] for k in ('title', 'url', 'text', 'start', 'end') if k in p}
                         for p in value.get('passages', [])],
            'results': [{**{k: row[k] for k in ('title', 'url', 'evidenceKind') if k in row},
                         **({'snippet': row.get('snippet', '')[:240]}
                            if row.get('evidenceKind') == 'search_snippet_only' else {})}
                        for row in value.get('results', [])],
        }.items() if v is not None}
    if name == "manage_goal":
        return {k: value[k] for k in ("applied", "operation", "goal", "reason", "changedSteps") if k in value}
    if name == "advance_goal":
        return {"available": value.get("available"), "reason": value.get("reason"),
                "goal": {k: value.get("goal", {})[k] for k in ("title", "status") if k in value.get("goal", {})},
                "reflection": {k: value.get("reflection", {})[k] for k in (
                    "feedback", "goalCompleted", "completionReason", "nextStep", "suggestedNextStep", "limitReached", "stepAdjustments", "adjustmentReason")
                    if k in value.get("reflection", {})}}
    if name == "sync_goal_progress":
        return {"applied": value.get("applied"), "reason": value.get("reason"),
                "updates": [{k: row[k] for k in ("goalTitle", "actionTitle", "outcome", "note", "planningStatus") if k in row}
                            for row in value.get("updates", [])]}
    if name == "memory_search":
        return {
            "status": value.get("status"),
            "purpose": value.get("purpose", "lookup"),
            "scope": value.get("scope"),
            "retrievalCoverage": value.get("retrievalCoverage", {}),
            "evidence": [
                {
                    k: c[k]
                    for k in ("role", "content", "sessionId", "observedAt", "observationDate", "recencyRank", "timestampRole", "truncated")
                    if k in c
                }
                for c in value.get("evidence", [])
            ],
            "candidateEvidence": [
                {k: c[k] for k in ("role", "content", "sessionId", "observedAt", "truncated") if k in c}
                for c in value.get("candidateEvidence", [])
            ],
        }
    if name == "get_dashboard":
        value = dashboard_with_availability(value)
        overview = value.get("conversationOverview") or {}
        return {
            "reportAvailability": value["reportAvailability"],
            "conversationOverview": {
                k: overview[k]
                for k in (
                    "status",
                    "periodStart",
                    "periodEnd",
                    "coverage",
                    "summary",
                    "themes",
                    "emotionObservations",
                )
                if k in overview
            },
            "reportSummaryResponse": value.get("reportSummaryResponse", value.get("reports", [])),
        }
    if name == "get_goal_context":
        return {
            "conversationGoal": next(({k: g[k] for k in ("title", "status") if k in g}
                for g in value.get("goals", []) if g.get("id") == value.get("conversationGoalId")), None),
            "totalGoalCount": len(value.get("goals", [])),
            "recentProgress": [
                {**{k: row[k] for k in ("goalTitle", "actionTitle", "outcome", "note", "createdAt") if k in row},
                 "saved": True}
                for row in value.get("recentProgress", [])[:8] if not row.get("undone")
            ],
            "goals": [
                {
                    k: g[k]
                    for k in (
                        "title",
                        "description",
                        "completionCriteria",
                        "status",
                        "actions",
                        "updatedAt",
                    )
                    if k in g
                }
                for g in value.get("goals", [])
            ],
            "actionLibrary": value.get("actionLibrary", []),
        }
    return value


def _shrink(value, estimator, budget):
    result = copy.deepcopy(value)
    truncated = False
    for _ in range(128):
        if estimator.count(json.dumps(result, ensure_ascii=False, default=str)) <= budget:
            break
        if (isinstance(result, dict) and 'passages' in result
                and len(result.get('results', [])) > 1):
            # Retain the first source and complete passages before secondary links.
            result['results'].pop()
            truncated = True
            continue
        if isinstance(result, dict) and result.get('passages'):
            # A whole source chunk is the unit of evidence, including its qualifications.
            result['passages'].pop()
            truncated = True
            if not result['passages']:
                result['reason'] = 'web_evidence_exceeds_remaining_context_budget'
            continue
        if isinstance(result, dict) and result.get("candidateEvidence"):
            # Never strip a later negation/correction from an unverified source.
            # If the complete quote cannot fit, remove that candidate as a unit.
            result["candidateEvidence"].pop()
            truncated = True
            continue
        if isinstance(result, dict) and result.get("goals") and result.get("actionLibrary"):
            result["actionLibrary"] = []
            truncated = True
            continue
        leaves, lists = [], []

        def visit(item, leaves=leaves, lists=lists):
            if isinstance(item, dict):
                for key, child in item.items():
                    if isinstance(child, str) and len(child) > 96 and key != "url":
                        leaves.append((len(child), item, key))
                    else:
                        visit(child)
            elif isinstance(item, list):
                if len(item) > 1:
                    lists.append((len(item), item))
                for index, child in enumerate(item):
                    if isinstance(child, str) and len(child) > 96:
                        leaves.append((len(child), item, index))
                    else:
                        visit(child)

        visit(result)
        if leaves:
            _, parent, key = max(leaves, key=lambda v: v[0])
            parent[key] = parent[key][: max(80, len(parent[key]) // 2)] + "…[截断]"
        elif lists:
            _, items = max(lists, key=lambda v: v[0])
            items.pop()
        else:
            result = {"unavailable": True, "reason": "result_exceeds_response_context_budget"}
            break
        truncated = True
    if truncated and isinstance(result, dict):
        result["contextTruncated"] = True
        if result.get("status") == "sufficient":
            result["status"] = "controlled"
        if "retrievalCoverage" in result:
            result["retrievalCoverage"]["contextCoverageUncertain"] = True
    return result


def bounded_tool_context(results, estimator, budget=800, *, audit=None):
    if not results:
        return "{}", False
    per_tool = max(128, (budget - 80) // len(results))
    projected = {
        name: _shrink(_project(name, value), estimator, per_tool) for name, value in results.items()
    }
    if audit is not None and isinstance(results.get("memory_search"), dict):
        before, after = results["memory_search"], projected.get("memory_search", {})
        audit.update({
            "memoryEvidenceBefore": len(before.get("evidence", [])),
            "memoryEvidenceInPrompt": len(after.get("evidence", [])),
            "memoryReviewBefore": len(before.get("candidateEvidence", [])),
            "memoryReviewInPrompt": len(after.get("candidateEvidence", [])),
            "memoryContextTruncated": after.get("contextTruncated", False),
        })
    text = json.dumps(projected, ensure_ascii=False, default=str)
    truncated = any(isinstance(v, dict) and v.get("contextTruncated") for v in projected.values())
    return text, truncated
