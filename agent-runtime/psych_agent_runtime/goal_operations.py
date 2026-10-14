"""Guarded goal operations using owned receipts, finite decisions and actual results."""

import logging
import re

from .goal_creation import allow_creation, explicit_creation
from .goal_progress import eligible_report, select_progress

log = logging.getLogger(__name__)


class GoalOperations:
    def __init__(self, tools, model, emit):
        self.tools = tools
        self.model = model
        self._emit = emit

    async def create(self, state: dict) -> dict:
        if (
            state.get("crisis")
            or (
                state.get("persistence_status") != "saved"
                and (state.get("turn_acceptance") or {}).get("accepted") is not True
            )
            or not explicit_creation(state["message"])
            or (state.get("goal_intent") or {}).get("intent") in {"none", "clarify"}
        ):
            return {"created_goals": []}
        try:
            focus = (state.get("plan") or {}).get("routing", {}).get("goalFocus")
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
                return {"created_goals": []}
            model = self.model
            semantic = state.get("goal_intent") or {}
            decision_message = (
                "请创建一个目标：" + semantic["goalText"]
                if semantic.get("intent") == "create"
                else state["message"]
            )
            if not await allow_creation(decision_message, model, has_owned_source=has_source):
                self._emit(
                    "warning",
                    {
                        "message": "这次没有确认目标创建意图，目标尚未保存。可以补充希望创建的具体目标内容。"
                    },
                )
                return {"created_goals": []}
            self._emit("status", {"node": "goal_creation", "label": "把目标拆成小步骤"})
            arguments = {
                "username": state["username"],
                "session_id": state["session_id"],
                "request_id": state["request_id"],
            }
            if has_source:
                arguments["source_goal_title"] = focus
            result = await self.tools.call("create_goal", arguments)
            created = [result["goal"]] if result.get("created") is True else []
            if created:
                self._emit("goal_created", {"goals": created})
            else:
                self._emit(
                    "warning", {"message": "目标尚未创建，可以在目标页手动添加或换一种表达。"}
                )
            return {"created_goals": created}
        except Exception:
            log.warning("Goal was not created", exc_info=True)
            self._emit("warning", {"message": "目标暂未创建，请稍后重试或在目标页手动添加。"})
            return {"created_goals": []}

    async def sync_progress(self, state: dict) -> dict:
        if (
            state.get("crisis")
            or explicit_creation(state["message"])
            or (
                state.get("persistence_status") != "saved"
                and (state.get("turn_acceptance") or {}).get("accepted") is not True
            )
            or not eligible_report(state["message"])
        ):
            return {"goal_updates": []}
        try:
            context = (state.get("tool_results") or {}).get("get_goal_context")
            if context is None:
                context = await self.tools.call("get_goal_context", {"username": state["username"]})
            if not context.get("autoSync", True):
                return {"goal_updates": []}
            selection = await select_progress(
                state["message"], context.get("goals", []), self.model
            )
            if selection is None:
                return {"goal_updates": []}
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
                },
            )
            updates = [result["checkIn"]] if result.get("applied") is True else []
            if updates:
                self._emit("goal_progress", {"updates": updates})
            return {"goal_updates": updates}
        except Exception:
            log.warning("Goal progress was not synced", exc_info=True)
            return {"goal_updates": []}
