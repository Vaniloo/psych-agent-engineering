"""One report availability vocabulary for both local and MCP tool paths."""


def dashboard_with_availability(result: dict) -> dict:
    if result.get("unavailable"):
        return result
    overview = result.get("conversationOverview") or {}
    reports = result.get("reportSummaryResponse", result.get("reports", []))
    coverage = overview.get("coverage") or {}
    expressions = coverage.get("expressionCount", 0)
    has_overview = (
        isinstance(expressions, int)
        and expressions > 0
        and overview.get("status") in {"ready", "limited", "fallback"}
        and bool(overview.get("summary") or overview.get("themes"))
    )
    availability = {
        "hasConversationOverview": has_overview,
        "hasFormalReports": bool(reports),
        "overviewLimited": overview.get("status") in {"limited", "fallback"}
        or coverage.get("activeDays", 0) < 2
        or coverage.get("conversationCount", 0) < 2
        or coverage.get("sampled") is True,
        "formalReportCount": len(reports),
        "evidenceScope": "conversation_overview_and_formal_reports_only",
        "answerKind": "overview" if has_overview else "formal_reports" if reports else "empty",
    }
    return {**result, "reportAvailability": availability}
