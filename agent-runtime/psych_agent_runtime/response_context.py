"""Keep tool facts in valid bounded JSON, rather than cutting a JSON string."""

import copy
import json

from .report_context import dashboard_with_availability


def _project(name, value):
    if not isinstance(value, dict) or value.get("unavailable"):
        return value
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
            "totalGoalCount": len(value.get("goals", [])),
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
        if isinstance(result, dict) and result.get("goals") and result.get("actionLibrary"):
            result["actionLibrary"] = []
            truncated = True
            continue
        leaves, lists = [], []

        def visit(item, leaves=leaves, lists=lists):
            if isinstance(item, dict):
                for key, child in item.items():
                    if isinstance(child, str) and len(child) > 96:
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


def bounded_tool_context(results, estimator, budget=800):
    if not results:
        return "{}", False
    per_tool = max(128, (budget - 80) // len(results))
    projected = {
        name: _shrink(_project(name, value), estimator, per_tool) for name, value in results.items()
    }
    text = json.dumps(projected, ensure_ascii=False, default=str)
    truncated = any(isinstance(v, dict) and v.get("contextTruncated") for v in projected.values())
    return text, truncated
