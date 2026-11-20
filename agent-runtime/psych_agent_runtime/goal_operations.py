"""Guarded goal operations using owned receipts, finite decisions and actual results."""

import json
import logging
import re

from .goal_creation import allow_creation, explicit_creation
from .goal_model_gate import active_decision, wants_create, wants_management, wants_progress
from .goal_progress import select_progress

log = logging.getLogger(__name__)


def can_write_turn(state):
    return not state.get("crisis") and (
        state.get("persistence_status") == "saved"
        or (state.get("turn_acceptance") or {}).get("accepted") is True
    )


class GoalOperations:
    def __init__(self, tools, model, emit):
        self.tools = tools
        self.model = model
        self._emit = emit

    async def create(self, state: dict) -> dict:
        gate = active_decision(state)
        if (
            not can_write_turn(state)
            or not wants_create(state)
            or (gate is None and (state.get("goal_intent") or {}).get("intent") in {"none", "clarify"})
        ):
            return {"created_goals": []}
        stage = "decision"
        try:
            focus = (state.get("plan") or {}).get("routing", {}).get("goalFocus") if gate is None else None
            owned = (state.get("tool_results") or {}).get("get_goal_context", {}).get("goals", [])
            has_source = bool(focus and any(g.get("title") == focus for g in owned))
            if (
                focus
                and not has_source
                and re.search(r"下一步|后续|接下来|下一个", state["message"])
            ):
                self._emit(
                    "warning",
                    {
                        "message": "这次没有读到原目标的步骤和反馈，后续目标尚未创建。请稍后重试或说明下一步想做什么。"
                    },
                )
                return {"created_goals": [], "goal_creation_result": {
                    "created": False, "reason": "source_goal_not_found",
                }}
            model = self.model
            semantic = state.get("goal_intent") or {}
            decision_message = (
                "请创建一个目标：" + semantic["goalText"]
                if semantic.get("intent") == "create"
                else state["message"]
            )
            if gate is None and not await allow_creation(decision_message, model, has_owned_source=has_source):
                self._emit(
                    "warning",
                    {
                        "message": "这次未通过目标创建确认，目标尚未保存。可以稍后重试。"
                    },
                )
                return {"created_goals": [], "goal_creation_result": {
                    "created": False, "reason": "intent_unconfirmed",
                }}
            self._emit("status", {"node": "goal_creation", "label": "把目标拆成小步骤"})
            arguments = {
                "username": state["username"],
                "session_id": state["session_id"],
                "request_id": state["request_id"],
            }
            if has_source:
                arguments["source_goal_title"] = focus
            if gate is not None:
                arguments["model_approval"] = gate["approval"]
            stage = "create"
            result = await self.tools.call("create_goal", arguments)
            created = [result["goal"]] if result.get("created") is True else []
            if created:
                self._emit("goal_created", {"goals": created})
                return {"created_goals": created}
            self._emit("warning", {"message": "目标尚未创建，可以在目标页手动添加或换一种表达。"})
            return {"created_goals": [], "goal_creation_result": {
                "created": False, "reason": result.get("reason", "not_created"),
            }}
        except Exception:
            log.warning("Goal was not created", exc_info=True)
            self._emit("warning", {"message": "目标服务暂时不可用，本次目标尚未创建，可以稍后重试。"})
            return {"created_goals": [], "goal_creation_result": {
                "created": False,
                "reason": "decision_unavailable" if stage == "decision" else "create_unavailable",
            }}

    async def sync_progress(self, state: dict) -> dict:
        gate = active_decision(state)
        if (
            not can_write_turn(state)
            or (gate is None and explicit_creation(state["message"]))
            or not wants_progress(state)
        ):
            return {"goal_updates": []}
        try:
            context = (state.get("tool_results") or {}).get("get_goal_context")
            if context is None:
                context = await self.tools.call("get_goal_context", {
                    "username": state["username"], "session_id": state["session_id"],
                    "request_id": state["request_id"],
                })
            if not context.get("autoSync", True):
                return {"goal_updates": []}
            selection = {**gate["selection"], "quote": state["message"]} if gate is not None else await select_progress(
                state["message"], context.get("goals", []), self.model,
                context=state.get("context"),
                conversation_goal_id=context.get("conversationGoalId"),
            )
            if selection is None:
                return {"goal_updates": [], "goal_sync_result": {
                    "applied": False, "reason": "unresolved_action",
                }}
            result = await self.tools.call(
                "sync_goal_progress",
                {
                    "username": state["username"],
                    "session_id": state["session_id"],
                    "request_id": state["request_id"],
                    "goal_id": selection["goalId"],
                    "action_id": selection["actionId"],
                    "version": selection["version"],
                    "outcome": selection["outcome"],
                    "quote": selection["quote"],
                    **({"model_approval": gate["approval"]} if gate is not None else {}),
                },
            )
            updates = [result["checkIn"]] if result.get("applied") is True else []
            if updates:
                completed = updates[0].get("outcome") == "done"
                self._emit("goal_progress", {"updates": [{**updates[0], "planningStatus": "pending"}] if completed else updates})
                if completed:
                    # Completion is already committed. Planning failure cannot erase it.
                    try:
                        self._emit("status", {"node": "goal_advancement", "label": "结合这次进展判断目标和下一步"})
                        advancement = await self.tools.call("advance_goal", {
                            "username": state["username"], "session_id": state["session_id"],
                            "request_id": state["request_id"], "goal_id": updates[0]["goalId"],
                            "check_in_id": updates[0]["id"],
                        })
                    except Exception:
                        log.warning("Goal completion saved; advancement unavailable", exc_info=True)
                        advancement = {"available": False, "reason": "unavailable"}
                    result["advancement"] = advancement
                    updates = [{**updates[0], "planningStatus": "ready" if advancement.get("available") is True else "unavailable",
                                **({"goal": advancement["goal"], "reflection": advancement["reflection"]}
                                   if advancement.get("available") is True else {})}]
                    self._emit("goal_progress", {"updates": updates})
            return {"goal_updates": updates, "goal_sync_result": result}
        except Exception:
            log.warning("Goal progress was not synced", exc_info=True)
            return {"goal_updates": [], "goal_sync_result": {
                "applied": False, "reason": "unavailable",
            }}

    async def manage(self, state: dict) -> dict:
        if not can_write_turn(state) or not wants_management(state):
            return {}
        gate = active_decision(state)
        try:
            selected = gate["selection"]
            result = await self.tools.call("manage_goal", {
                "username": state["username"], "session_id": state["session_id"], "request_id": state["request_id"],
                "goal_id": selected["goalId"], "version": selected["version"], "operation": gate["operation"],
                "edits_json": gate["editsJson"], "model_approval": gate["approval"],
            })
            if result.get("applied") is True and gate["operation"] == "revise":
                changed_ids = {patch["actionId"] for patch in json.loads(gate["editsJson"])}
                result["changedSteps"] = [{"position": i + 1, "title": action["title"]}
                    for i, action in enumerate(result["goal"]["actions"]) if action["id"] in changed_ids]
            updates = [{**result["checkIn"], "goal": result["goal"]}] if result.get("applied") is True else []
            if updates:
                self._emit("goal_progress", {"updates": updates})
            return {"goal_updates": updates, "goal_management_result": result}
        except Exception:
            log.warning("Goal management was not saved", exc_info=True)
            return {"goal_updates": [], "goal_management_result": {"applied": False, "reason": "unavailable"}}
