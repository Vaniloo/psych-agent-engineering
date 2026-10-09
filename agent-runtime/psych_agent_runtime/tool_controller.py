"""Second-stage local Laya controller, constrained by the first-stage policy."""

from __future__ import annotations

import json
import logging

import httpx

from .config import Settings
from .decision_contract import EXECUTOR_CONTRACT, EXECUTOR_QUESTIONS, TOOL_CHOICES
from .decision_router import LocalDecisions
from .schemas import Plan

log = logging.getLogger(__name__)


class ToolController:
    def __init__(self, settings: Settings):
        if settings.executor_provider not in {"rules", "laya"}:
            raise ValueError("EXECUTOR_PROVIDER must be rules or laya")
        self.model = (
            LocalDecisions(
                settings.executor_base_url,
                settings.executor_api_key,
                settings.executor_timeout_seconds,
                EXECUTOR_CONTRACT,
                EXECUTOR_QUESTIONS,
                settings.executor_min_probability,
            )
            if settings.executor_provider == "laya"
            else None
        )

    async def close(self):
        if self.model:
            await self.model.close()

    async def run(self, plan: Plan, message: str, invoke, emit) -> tuple[dict, list]:
        results, trace = {}, []
        # Permission sets originate in route_policy, never in raw model output.
        allowed = list(dict.fromkeys(plan.allowed_tools))
        required = list(dict.fromkeys(plan.required_tools))
        if any(name not in allowed for name in required):
            raise ValueError("required capability is not authorized")
        if len(required) > plan.max_tool_calls:
            raise ValueError("required capabilities exceed budget")
        defaults = [item["name"] for item in plan.tools if item["name"] in allowed]
        for step in range(plan.max_tool_calls + 1):
            remaining = [name for name in allowed if name not in results]
            compulsory = [name for name in required if name not in results]
            fallback = next((name for name in defaults if name in remaining), "finish")
            action, source, reason = (
                (compulsory[0], "policy", "") if compulsory else (fallback, "rules", "")
            )
            if step >= plan.max_tool_calls:
                action, reason = "finish", "budget_exhausted"
            elif self.model and not compulsory:
                labels = {name: TOOL_CHOICES[name] for name in [*remaining, "finish", "clarify"]}
                state = {
                    "request": message[:4000],
                    "workflow": plan.intent,
                    "allowedTools": remaining,
                    "remaining_calls": plan.max_tool_calls - step,
                    "observations": {
                        name: json.dumps(value, ensure_ascii=False, default=str)[:1000]
                        for name, value in results.items()
                    },
                }
                try:
                    answer = (
                        await self.model.evaluate(
                            state, ["next_action"], labels_by_task={"next_action": labels}
                        )
                    )["decisions"]["next_action"]
                    if answer["accepted"]:
                        action, source = answer["choice"], "laya"
                    else:
                        reason = "decision_abstained"
                except (httpx.HTTPError, ValueError, TypeError, KeyError):
                    reason = "executor_unavailable_or_invalid"
            item = {"step": step + 1, "action": action, "provider": source, "fallback": reason}
            trace.append(item)
            emit("tool_decision", item)
            if action in {"finish", "clarify"}:
                break
            if action not in remaining:
                raise ValueError("executor attempted unauthorized or repeated capability")
            try:
                results[action] = await invoke(action)
            except Exception:
                log.warning("Capability %s failed", action, exc_info=True)
                results[action] = {"unavailable": True}
        return results, trace
