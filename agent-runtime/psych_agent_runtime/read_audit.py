"""Content-free review signals; retrieval misses are not proven failures."""

from .personal_recall import personal_recall_request
from .route_policy import explicit_memory_request


def read_audit(state):
    plan = state.get("plan") or {}
    result = (state.get("tool_results") or {}).get("memory_search")
    requested = bool(personal_recall_request(state["message"], state.get("context"))
                     or explicit_memory_request(state["message"]) or plan.get("intent") == "memory")
    signals = []
    prompt = (state.get("context_stats") or {}).get("toolEvidence", {})
    if requested and result is None:
        signals.append("memory_read_missing")
    if requested and isinstance(result, dict):
        coverage = result.get("retrievalCoverage", {})
        if result.get("unavailable") or result.get("status") == "unavailable":
            signals.append("memory_read_unavailable")
        elif not result.get("evidence"):
            signals.append("memory_no_accepted_evidence")
        if result.get("candidateEvidence"):
            signals.append("memory_evidence_needs_review")
        if coverage.get("missing") or coverage.get("contextCoverageUncertain"):
            signals.append("memory_partial_coverage")
        if prompt.get("memoryContextTruncated"):
            signals.append("memory_response_context_reduced")
    memory = result if isinstance(result, dict) else {}
    loop = state.get("loop") or {}
    raw_sources = memory.get("audit", {})
    sources = {k: raw_sources[k] for k in (
        "candidateSourceIds", "acceptedSourceIds", "reviewSourceIds", "acceptedBeforeDelivery", "deliveryLimit"
    ) if k in raw_sources}
    return {"version": "read-review-v1", "classification": "suspected_requires_review",
            "signals": signals, "memoryRequested": requested, "memoryCalled": result is not None,
            "allowedReads": list(loop.get("allowed", plan.get("allowed_tools", []))),
            "requiredReads": list(loop.get("required", plan.get("required_tools", []))),
            "actions": [t["action"] for t in state.get("tool_trace", []) if "action" in t],
            "memoryStatus": memory.get("status"), "controller": memory.get("controller"),
            "evidenceCount": len(memory.get("evidence", [])),
            "reviewCount": len(memory.get("candidateEvidence", [])),
            "coverage": {k: memory.get("retrievalCoverage", {}).get(k) for k in ("requested", "covered", "missing")},
            "sources": sources, "promptEvidence": {k: prompt[k] for k in (
                "memoryEvidenceBefore", "memoryEvidenceInPrompt", "memoryReviewBefore",
                "memoryReviewInPrompt", "memoryContextTruncated") if k in prompt}}
