"""Contextual goal decisions; shadow mode or request-bound model authorization."""

import logging

from .decision_router import LocalDecisions
from .goal_gate_contract import CONTRACT, INSTRUCTIONS
from .goal_management import inspect_management
from .goal_model_approval import sign_approval
from .goal_write_review import GoalWriteReviewer

QUESTIONS = {task: {"criteria": {"yes": instruction, "no": "原话及上下文不满足该条件"}}
             for task, instruction in INSTRUCTIONS.items()}
log = logging.getLogger(__name__)


def active_decision(state):
    decision = state.get("goal_gate_preview") or {}
    return decision if decision.get("mode") == "model" else None


def wants_create(state):
    gate = active_decision(state)
    if gate is not None:
        return gate.get("operation") == "create" and bool(gate.get("approval"))
    from .goal_creation import explicit_creation
    return explicit_creation(state["message"])


def wants_progress(state):
    gate = active_decision(state)
    if gate is not None:
        return gate.get("operation") == "progress" and bool(gate.get("approval"))
    from .goal_progress import eligible_report
    return eligible_report(state["message"])


def wants_management(state):
    gate = active_decision(state)
    return bool(gate and gate.get("operation") in {"revise", "complete", "pause"} and gate.get("approval"))


def positive(decision):
    return decision.get("accepted") is True and decision.get("choice") == "yes"


def unambiguous_positive(decisions):
    if not decisions or not all(d.get("accepted") is True for d in decisions):
        return None
    found = [i for i, d in enumerate(decisions) if positive(d)]
    return found[0] if len(found) == 1 else None


class GoalModelGate:
    def __init__(self, settings, backend, generation=None):
        self.mode = settings.goal_gate_mode
        if self.mode not in {"off", "shadow", "model"}:
            raise ValueError("invalid goal gate mode")
        self.users = set(filter(None, (s.strip() for s in settings.goal_gate_users.split(","))))
        if "*" in self.users:
            raise ValueError("goal preview requires explicit account allowlist")
        self.settings = settings
        if self.mode == "model" and (not self.users or len(settings.goal_gate_expected_sha) != 64
                                      or not settings.internal_service_token or not settings.goal_gate_api_key):
            raise ValueError("model mode needs explicit users, pinned weights and private credentials")
        self.backend = backend
        self.reviewer = GoalWriteReviewer(generation)
        self.controller = LocalDecisions(settings.goal_gate_base_url, settings.goal_gate_api_key,
                                         settings.goal_gate_timeout_seconds, CONTRACT, QUESTIONS, 0.90)

    async def close(self):
        await self.controller.close()

    async def inspect(self, username, session_id, request_id, message, context):
        if self.mode == "off" or username not in self.users:
            return None
        if not 1 <= len(message) <= 2000:
            return {"mode": self.mode, "status": "abstained", "reason": "input_budget", "writes": False}
        history = [{"role": m.role, "content": m.content[:350]} for m in context.messages[-4:]
                   if m.role in {"user", "assistant"}]
        state = {"message": message, "history": history, "candidates": []}
        try:
            if self.mode == "model":
                response = await self.controller.client.get("/health")
                response.raise_for_status()
                health = response.json()
                if (health.get("ready") is not True or health.get("profile") != "goal"
                        or health.get("contractVersion") != CONTRACT
                        or health.get("artifact", {}).get("adapterSha256") != self.settings.goal_gate_expected_sha):
                    raise ValueError("goal gate weights or contract not pinned")
            owned = await self.backend.goals(username, session_id=session_id, request_id=request_id)
            focus = owned.get("conversationGoalId")
            goals = [g for g in owned.get("goals", []) if g["status"] == "active"]
            candidates = [{"goalId": g["id"], "actionId": a["id"], "version": g["version"],
                           "goalTitle": g["title"], "actionTitle": a["title"], "position": i + 1,
                           "focused": g["id"] == focus, "status": a["status"]}
                          for g in goals for i, a in enumerate(g["actions"]) if a["status"] == "planned"]
            state["candidates"] = candidates[:8]
            gates = await self.controller.evaluate(state, ["goal_related", "create_request", "progress_report"])
            if self.mode == "model" and gates["model"] != health.get("model"):
                raise ValueError("goal gate model changed during request")
            decisions = gates["decisions"]
            result = {"mode": self.mode, "status": "scored", "model": gates["model"], "writes": False,
                      "gates": decisions, "operation": "none"}
            if self.mode == "model" and positive(decisions.get("goal_related", {})):
                management = {"operation": "unavailable"} if self.reviewer.disabled else await inspect_management(
                    self.reviewer.generation, message, history, goals, focus)
                if management.get("disable"):
                    self.reviewer.disabled = True
                if management["operation"] in {"revise", "complete", "pause"}:
                    if owned.get("autoSync") is False:
                        return {**result, "status": "abstained", "reason": "auto_sync_disabled"}
                    return {**result, **management, "approval": sign_approval(
                        self.settings.internal_service_token, self.settings.goal_gate_expected_sha,
                        username, session_id, request_id, message, management["operation"], management["selection"])}
                if management["operation"] in {"clarify", "unavailable"}:
                    return {**result, "operation": "clarify" if management["operation"] == "clarify" else "none",
                            "status": "abstained" if management["operation"] == "clarify" else "unavailable",
                            "reason": "management_" + management["operation"]}
            if not all(d.get("accepted") is True for d in decisions.values()):
                return {**result, "status": "abstained", "reason": "gate_uncertain"}
            if not positive(decisions["goal_related"]):
                return result
            create, progress = (positive(decisions[t]) for t in ("create_request", "progress_report"))
            if create and progress:
                return {**result, "status": "abstained", "operation": "clarify", "reason": "conflicting_intents"}
            if create:
                result["operation"] = "create"
                if self.mode == "model":
                    review = await self.reviewer.verify(state, {"operation": "create"})
                    result["review"] = review
                    if review["status"] != "confirmed":
                        return {**result, "status": "unavailable" if review["status"] == "unavailable" else "abstained",
                                "operation": "clarify" if review["status"] == "clarify" else "none",
                                "reason": "write_review_" + review["status"]}
                    result["approval"] = sign_approval(self.settings.internal_service_token,
                        self.settings.goal_gate_expected_sha, username, session_id, request_id, message, "create")
                return result
            if not progress:
                return {**result, "operation": "read"}
            if len(message) > 500:
                return {**result, "status": "abstained", "operation": "clarify", "reason": "progress_budget"}
            if owned.get("autoSync") is False:
                return {**result, "status": "abstained", "reason": "auto_sync_disabled"}
            if not 1 <= len(candidates) <= 8:
                return {**result, "status": "abstained", "operation": "clarify", "reason": "candidate_budget"}
            # The contract removes all IDs before sending text to the scorer.
            state["candidates"] = candidates
            matches = await self.controller.evaluate_many(
                [{**state, "candidateIndex": i} for i in range(len(candidates))], "candidate_match")
            result.update(candidateDecisions=matches, candidateCount=len(candidates))
            index = unambiguous_positive(matches)
            if index is None:
                if self.mode == "model":
                    return await self._review_progress(state, result, candidates, (username, session_id, request_id))
                return {**result, "status": "abstained", "operation": "clarify", "reason": "unresolved_step"}
            if candidates[index]["status"] != "planned":
                return {**result, "status": "abstained", "operation": "clarify", "reason": "unresolved_step"}
            tasks = ["outcome_done", "outcome_partial", "outcome_blocked", "outcome_rest"]
            outcomes = (await self.controller.evaluate(state, tasks))["decisions"]
            result["outcomes"] = outcomes
            outcome = unambiguous_positive([outcomes[t] for t in tasks])
            if outcome is None:
                if self.mode == "model":
                    return await self._review_progress(state, result, candidates, (username, session_id, request_id), index=index)
                return {**result, "status": "abstained", "operation": "clarify", "reason": "outcome_uncertain"}
            if self.mode == "model":
                return await self._review_progress(state, result, candidates, (username, session_id, request_id), index, tasks[outcome].removeprefix("outcome_"))
            selected = candidates[index]
            result.update(operation="progress", selection={
                k: selected[k] for k in ("goalId", "actionId", "version")})
            result["selection"]["outcome"] = tasks[outcome].removeprefix("outcome_")
            return result
        except Exception:
            # Model mode fails closed; shadow failures leave the baseline unchanged.
            log.warning("Goal gate preview unavailable", exc_info=True)
            return {"mode": self.mode, "status": "unavailable", "reason": "goal_gate_unavailable", "writes": False}

    async def _review_progress(self, state, result, candidates, binding, index=None, outcome=None):
        review = await self.reviewer.verify(state, {"operation": "progress", "candidateIndex": index, "outcome": outcome})
        result = {**result, "review": review}
        if review["status"] != "confirmed":
            return {**result, "status": "unavailable" if review["status"] == "unavailable" else "abstained",
                    "operation": "clarify" if review["status"] == "clarify" else "none",
                    "reason": "write_review_" + review["status"]}
        chosen = review["candidateIndex"]
        selected = {k: candidates[chosen][k] for k in ("goalId", "actionId", "version")}
        selected["outcome"] = review["outcome"]
        # Binding metadata is supplied by inspect, never sent to either model.
        owner, session_id, request_id = binding
        return {**result, "operation": "progress", "selection": selected,
                "resolution": "model_review" if index is None or outcome is None else "agreement",
                "approval": sign_approval(self.settings.internal_service_token,
                    self.settings.goal_gate_expected_sha, owner, session_id, request_id, state["message"],
                    "progress", selected)}


def public_summary(result):
    if not result:
        return None
    return {**{k: result[k] for k in ("mode", "status", "model", "operation", "reason", "candidateCount", "resolution")
               if k in result},
            **({"reviewStatus": result["review"]["status"]} if result.get("review") else {})}
