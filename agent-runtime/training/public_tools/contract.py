"""Training objectives; rendering is shared with the deployable runtime package."""

from psych_agent_runtime.public_tool_contract import render, specification

__all__ = ["loss", "metrics", "render", "specification"]


def loss(torch, scores, row):
    # Multi-call blocks supervise all distinct observed tools equally; no utility BCE.
    # Other candidates compete for demonstration choice, not absolute suitability.
    return -torch.log_softmax(scores, dim=0)[row["targetIndices"]].mean()


def metrics(rows):
    def subset(part):
        if not part:
            return {"rows": 0, "top1DemonstrationAgreement": None}
        correct = sum(max(range(len(r["scores"])), key=r["scores"].__getitem__) in r["targetIndices"] for r in part)
        return {"rows": len(part), "top1DemonstrationAgreement": correct / len(part)}

    informative = [r for r in rows if r["informativeChoice"]]
    return {"all": subset(rows), "informative": subset(informative),
            "observedToolCall": subset([r for r in informative if r["observedAction"] == "tool_call"]),
            "observedAssistantText": subset([r for r in informative if r["observedAction"] == "assistant_text"]),
            "singleActionOnlyRows": len(rows) - len(informative),
            "scope": "agreement_with_publisher_next_action_not_absolute_tool_utility_or_end_to_end_success"}
