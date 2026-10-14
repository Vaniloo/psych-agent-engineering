"""Tool and goal decisions can use separate workers under the same permission policy."""

from __future__ import annotations

import asyncio
import json
import logging
import time

import httpx

from .config import Settings
from .decision_contract import EXECUTOR_CONTRACT, EXECUTOR_QUESTIONS, TOOL_CHOICES
from .decision_router import LocalDecisions
from .schemas import Plan

log = logging.getLogger(__name__)


class ToolController:
    def __init__(self, settings: Settings):
        self.settings = settings
        if settings.executor_provider not in {"rules", "laya", "qwen"}:
            raise ValueError("EXECUTOR_PROVIDER must be rules, laya or qwen")
        if settings.executor_goal_provider not in {"", "rules", "laya", "qwen"}:
            raise ValueError("EXECUTOR_GOAL_PROVIDER must be empty, rules, laya or qwen")
        if (
            settings.executor_goal_provider in {"laya", "qwen"}
            and not settings.executor_goal_base_url
        ):
            raise ValueError("EXECUTOR_GOAL_BASE_URL is required for a separate goal worker")
        self.model = (
            LocalDecisions(
                settings.executor_base_url,
                settings.executor_api_key,
                settings.executor_timeout_seconds,
                EXECUTOR_CONTRACT,
                EXECUTOR_QUESTIONS,
                settings.executor_min_probability,
            )
            if settings.executor_provider in {"laya", "qwen"}
            else None
        )
        self._goal_model = (
            LocalDecisions(
                settings.executor_goal_base_url,
                settings.executor_goal_api_key,
                settings.executor_goal_timeout_seconds,
                EXECUTOR_CONTRACT,
                {k: v for k, v in EXECUTOR_QUESTIONS.items() if k != "next_action"},
                settings.executor_goal_min_probability,
            )
            if settings.executor_goal_provider in {"laya", "qwen"}
            else None
        )

    @property
    def goal_model(self):
        return self._goal_model if self.settings.executor_goal_provider else self.model

    async def close(self):
        if self.model:
            await self.model.close()
        if self._goal_model:
            await self._goal_model.close()

    def start_loop(self, plan: Plan, message: str, arguments: dict | None = None) -> dict:
        from .capabilities import validate_arguments

        allowed = list(dict.fromkeys(plan.allowed_tools))
        required = list(dict.fromkeys(plan.required_tools))
        if any(name not in TOOL_CHOICES or name in {"finish", "clarify"} for name in allowed):
            raise ValueError("only registered read capabilities may enter the decision loop")
        if any(name not in allowed for name in required):
            raise ValueError("required capability is not authorized")
        if len(required) > plan.max_tool_calls:
            raise ValueError("required capabilities exceed budget")
        return {
            "request": message[:4000],
            "workflow": plan.intent,
            "allowed": allowed,
            "required": required,
            "defaults": [item["name"] for item in plan.tools if item["name"] in allowed],
            "arguments": {n: validate_arguments(n, a) for n, a in arguments.items()}
            if arguments is not None
            else {},
            "budget": plan.max_tool_calls,
            "deadline": time.time() + max(1, self.settings.agent_loop_timeout_seconds),
            "results": {},
            "observations": [],
            "trace": [],
            "seen": [],
            "calls": 0,
            "refinement_used": False,
            "phase": "read",
        }

    async def decide(self, loop: dict, emit, refiner=None) -> dict:
        loop = {**loop, "trace": list(loop["trace"])}
        results = loop["results"]
        remaining = [name for name in loop["allowed"] if name not in results]
        compulsory = [name for name in loop["required"] if name not in results]
        fallback = next((name for name in loop["defaults"] if name in remaining), "finish")
        action, source, reason = (
            (compulsory[0], "policy", "") if compulsory else (fallback, "rules", "")
        )
        arguments = loop["arguments"].get(action, {})
        if time.time() >= loop["deadline"]:
            action, reason = "finish", "deadline_exhausted"
        elif loop["calls"] >= loop["budget"]:
            action, reason = "finish", "budget_exhausted"
        elif not remaining:
            action, source = "finish", "policy"
        elif self.model and not compulsory:
            labels = {name: TOOL_CHOICES[name] for name in [*remaining, "finish", "clarify"]}
            state = {
                "request": loop["request"],
                "workflow": loop["workflow"],
                "allowedTools": remaining,
                "remaining_calls": loop["budget"] - loop["calls"],
                "observations": {
                    name: json.dumps(value, ensure_ascii=False, default=str)[:1000]
                    for name, value in results.items()
                },
            }
            try:
                async with asyncio.timeout(max(0.001, loop["deadline"] - time.time())):
                    reply = await self.model.evaluate(
                        state, ["next_action"], labels_by_task={"next_action": labels}
                    )
                answer = reply["decisions"]["next_action"]
                if answer["accepted"]:
                    action, source = (
                        answer["choice"],
                        reply.get("provider", self.settings.executor_provider),
                    )
                else:
                    reason = "decision_abstained"
            except (httpx.HTTPError, ValueError, TypeError, KeyError, TimeoutError):
                reason = "executor_unavailable_or_invalid"
            if action not in [*remaining, "finish", "clarify"]:
                raise ValueError("executor attempted unauthorized or repeated capability")
            arguments = loop["arguments"].get(action, {})
        # One bounded recovery may revise an authorized query, never identity or writes.
        if (
            action == "finish"
            and not compulsory
            and refiner
            and loop["budget"] > 0
            and loop["calls"] < 4
            and not loop["refinement_used"]
            and time.time() < loop["deadline"]
            and any(o["status"] in {"empty", "unavailable"} for o in loop["observations"])
        ):
            loop["refinement_used"] = True
            try:
                async with asyncio.timeout(min(8, max(0.001, loop["deadline"] - time.time()))):
                    recovery = await refiner(loop)
                if recovery:
                    name, patch = recovery["tool"], recovery["arguments"]
                    if name not in loop["allowed"]:
                        raise ValueError("recovery attempted unauthorized capability")
                    arguments = self.recovery_arguments(
                        name, loop["arguments"].get(name, {}), patch
                    )
                    key = self.call_key(name, arguments)
                    if key not in loop["seen"]:
                        action, source, reason = name, "structured_recovery", "query_refined"
                        loop["budget"] = min(4, max(loop["budget"], loop["calls"] + 1))
            except (httpx.HTTPError, ValueError, TypeError, KeyError, TimeoutError):
                reason = "recovery_unavailable_or_invalid"
        if action not in {"finish", "clarify"} and self.call_key(action, arguments) in loop["seen"]:
            action, reason = "finish", "duplicate_call_blocked"
        item = {
            "step": len(loop["trace"]) + 1,
            "action": action,
            "provider": source,
            "fallback": reason,
            "remainingCalls": max(0, loop["budget"] - loop["calls"]),
        }
        loop["trace"].append(item)
        loop["next"] = {"tool": action, "arguments": arguments}
        emit("tool_decision", item)
        return loop

    @staticmethod
    def recovery_arguments(name: str, original: dict, patch: dict) -> dict:
        from .capabilities import validate_arguments

        fields = {
            "search_knowledge": {"query"},
            "memory_search": {"query"},
            "get_goal_context": {"title", "status"},
        }.get(name, set())
        if not isinstance(patch, dict) or not patch or not set(patch) <= fields:
            raise ValueError("only retrieval filters can be revised")
        if (
            original.get("status") is not None
            and patch.get("status", original["status"]) != original["status"]
        ):
            raise ValueError("recovery cannot broaden an explicit goal status filter")
        return validate_arguments(name, {**original, **patch})

    @staticmethod
    def call_key(name: str, arguments: dict) -> str:
        return json.dumps([name, arguments], sort_keys=True, ensure_ascii=False, default=str)

    async def execute(self, loop: dict, invoke) -> dict:
        call = loop["next"]
        try:
            async with asyncio.timeout(max(0.001, loop["deadline"] - time.time())):
                result = await invoke(
                    call["tool"],
                    {
                        k: v
                        for k, v in call["arguments"].items()
                        if v is not None and (k != "recent_messages" or v)
                    },
                )
        except Exception:
            log.warning("Capability %s failed", call["tool"], exc_info=True)
            result = {"unavailable": True}
        return {**loop, "pending_result": result}

    def observe(self, loop: dict, emit) -> dict:
        loop = {
            **loop,
            "results": dict(loop["results"]),
            "observations": list(loop["observations"]),
            "seen": list(loop["seen"]),
        }
        name, arguments = loop["next"]["tool"], loop["next"]["arguments"]
        result = loop.pop("pending_result")
        status = "ok"
        if isinstance(result, dict):
            if result.get("unavailable") or result.get("status") == "unavailable":
                status = "unavailable"
            elif (
                name in {"search_knowledge", "memory_search"}
                and not result.get("hits", result.get("evidence"))
                or name == "get_goal_context"
                and not result.get("goals")
            ):
                status = "empty"
        observation = {"tool": name, "arguments": arguments, "status": status, "result": result}
        loop["observations"].append(observation)
        loop["seen"].append(self.call_key(name, arguments))
        loop["calls"] += 1
        # Keep useful earlier evidence if a corrected query fails or returns nothing.
        previous = loop["results"].get(name)
        useful_previous = isinstance(previous, dict) and bool(
            previous.get("hits", previous.get("evidence", previous.get("goals", [])))
        )
        if not useful_previous or status == "ok":
            loop["results"][name] = result
        emit("loop_observation", {"tool": name, "status": status, "calls": loop["calls"]})
        return loop
