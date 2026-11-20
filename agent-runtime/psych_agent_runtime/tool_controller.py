"""Select the next authorized read using ToolACE or host rules."""

from __future__ import annotations

import asyncio
import json
import logging
import time

import httpx

from .config import Settings
from .public_tool_client import PublicToolDecisions
from .schemas import Plan

log = logging.getLogger(__name__)

# Policy-authorized extensions share the loop, but are not new trained choice labels.
POLICY_READ_TOOLS = {"search_web", "search_role_voice"}


class ToolController:
    def __init__(self, settings: Settings):
        self.settings = settings
        if settings.executor_provider not in {"public_qwen", "rules"}:
            raise ValueError("EXECUTOR_PROVIDER must be public_qwen or rules")
        self.model = PublicToolDecisions(settings) if settings.executor_provider == "public_qwen" else None

    async def close(self):
        if self.model:
            await self.model.close()

    def start_loop(self, plan: Plan, message: str, arguments: dict | None = None) -> dict:
        from .capabilities import CAPABILITIES, validate_arguments

        allowed = list(dict.fromkeys(plan.allowed_tools))
        required = list(dict.fromkeys(plan.required_tools))
        if len(allowed) > 16 or any(name not in CAPABILITIES or not CAPABILITIES[name].is_read for name in allowed):
            raise ValueError("only registered read capabilities may enter the decision loop")
        if any(name in POLICY_READ_TOOLS and name not in required for name in allowed):
            raise ValueError("web reads require explicit policy authorization")
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
            "ceiling": min(16, max(plan.max_tool_calls, self.settings.agent_loop_max_tool_calls)),
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
        remaining = [name for name in loop["allowed"]
                     if name not in results or self.repeatable_read(name)]
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
        elif loop.get("knowledge", {}).get("retryQuery") and not compulsory:
            from .agentic_rag import MAX_READS, query_key
            knowledge = loop["knowledge"]
            retry = knowledge.pop("retryQuery")
            if ("search_knowledge" in loop["allowed"] and knowledge["rounds"] < MAX_READS
                    and time.time() < knowledge["deadline"]
                    and query_key(retry) not in {query_key(q) for q in knowledge["queries"]}):
                arguments = self.recovery_arguments("search_knowledge", loop["arguments"]["search_knowledge"],
                                                    {"query": retry})
                loop["arguments"] = {**loop["arguments"], "search_knowledge": arguments}
                action, source, reason = "search_knowledge", "agentic_rag", "evidence_gap"
            else:
                action, source, reason = "finish", "agentic_rag", "knowledge_retry_blocked"
                knowledge["stopReason"] = "retry_blocked"
        elif not remaining:
            action, source = "finish", "policy"
        elif self.model and not compulsory:
            state = {
                "request": loop["request"],
                "workflow": loop["workflow"],
                "allowedTools": remaining,
                "remaining_calls": loop["budget"] - loop["calls"],
                "observations": {
                    name: json.dumps(value, ensure_ascii=False, default=str)[
                        :4000 if self.repeatable_read(name) else 1000]
                    for name, value in self.result_context(loop).items()
                },
            }
            if loop.get("history"):
                state["history"] = loop["history"]
            try:
                async with asyncio.timeout(max(0.001, loop["deadline"] - time.time())):
                    reply = await self.model.evaluate(state, ["next_action"])
                answer = reply["decisions"]["next_action"]
                if answer["accepted"]:
                    action, source = (
                        answer["choice"],
                        reply.get("provider", self.settings.executor_provider),
                    )
                    reason = reply.get("fallbackReason", "")
                else:
                    reason = "decision_abstained"
            except (httpx.HTTPError, ValueError, TypeError, KeyError, TimeoutError):
                reason = "executor_unavailable_or_invalid"
            if action not in [*remaining, "finish", "clarify"]:
                raise ValueError("executor attempted unauthorized or repeated capability")
            arguments = loop["arguments"].get(action, {})
        if reason in {"deadline_exhausted", "budget_exhausted"} and loop.get("knowledge", {}).get("retryQuery"):
            loop["knowledge"].pop("retryQuery")
            loop["knowledge"]["stopReason"] = reason
        # One bounded recovery may revise an authorized query, never identity or writes.
        if (
            action == "finish"
            and not compulsory
            and refiner
            and loop["budget"] > 0
            and loop["calls"] < loop["ceiling"]
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
                    if name == "search_knowledge" and loop.get("knowledge"):
                        raise ValueError("knowledge recovery is owned by bounded evidence review")
                    if name not in loop["allowed"]:
                        raise ValueError("recovery attempted unauthorized capability")
                    arguments = self.recovery_arguments(
                        name, loop["arguments"].get(name, {}), patch
                    )
                    key = self.call_key(name, arguments)
                    if key not in loop["seen"]:
                        action, source, reason = name, "structured_recovery", "query_refined"
                        loop["budget"] = min(loop["ceiling"], max(loop["budget"], loop["calls"] + 1))
            except (httpx.HTTPError, ValueError, TypeError, KeyError, TimeoutError):
                reason = "recovery_unavailable_or_invalid"
        # Dynamic parameters are generated after selection. An old argument
        # value must not exclude a tool before its next arguments are resolved.
        if (action not in {"finish", "clarify"} and not self.repeatable_read(action)
                and self.call_key(action, arguments) in loop["seen"]):
            action, reason = "finish", "duplicate_call_blocked"
        item = {
            "step": len(loop["trace"]) + 1,
            "action": action,
            "provider": source,
            "fallback": reason,
            "remainingCalls": max(0, loop["budget"] - loop["calls"]),
            "calls": loop["calls"],
            "budget": loop["budget"],
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
        def canonical(value):
            if isinstance(value, dict):
                return {k: canonical(v) for k, v in value.items()}
            if isinstance(value, list):
                return [canonical(v) for v in value]
            # JSON numbers 30 and 30.0 denote the same parameter value.
            if type(value) is float and value.is_integer():
                return int(value)
            return value
        return json.dumps([name, canonical(ToolController.dispatch_arguments(arguments))], sort_keys=True, ensure_ascii=False,
                          default=str, allow_nan=False)

    @staticmethod
    def dispatch_arguments(arguments):
        return {k: v for k, v in arguments.items()
                if v is not None and (k != "recent_messages" or v)}

    @staticmethod
    def repeatable_read(name):
        from .capabilities import CAPABILITIES
        from .dynamic_tools import BOUND

        cap = CAPABILITIES.get(name)
        if not (cap and cap.is_read and cap.handler is not None and cap.replay_safe):
            return False
        # An argument-free or fully host-bound read has no different call to
        # make within this turn; retain its existing single-call fast path.
        return bool(set(cap.input_schema().get("properties", {})) - BOUND - set(dict(cap.bindings)))

    @classmethod
    def result_context(cls, loop, *, include_single=True):
        """Keep each dynamic call's arguments and outcome, not just its last result."""
        from .capabilities import CAPABILITIES
        from .dynamic_tools import BOUND

        results = dict(loop["results"])
        for name in results:
            if not cls.repeatable_read(name):
                continue
            hidden = BOUND | {k for k, source in CAPABILITIES[name].bindings if source in BOUND}
            calls = [{"arguments": {k: v for k, v in row["arguments"].items() if k not in hidden},
                      "status": row["status"], "result": row["result"]}
                     for row in loop["observations"] if row["tool"] == name]
            if calls and (include_single or len(calls) > 1):
                results[name] = {"available": any(c["status"] == "ok" for c in calls),
                                 "calls": calls}
        return results

    async def execute(self, loop: dict, invoke) -> dict:
        call = loop["next"]
        from .capabilities import CAPABILITIES
        cap = CAPABILITIES.get(call["tool"])
        # Validate again immediately before dispatch, including after a slow
        # argument generation. A stale/forged next decision cannot spend more
        # calls, repeat an identical call, or broaden permissions.
        reason = (
            "unauthorized_call" if call["tool"] not in loop["allowed"] or not cap or not cap.is_read
            else "duplicate_call_blocked" if self.call_key(call["tool"], call["arguments"]) in loop["seen"]
            else "budget_exhausted" if loop["calls"] >= loop["budget"]
            else "deadline_exhausted" if time.time() >= loop["deadline"]
            else None
        )
        if reason:
            return {**loop, "pending_result": {"unavailable": True, "reason": reason}}
        try:
            async with asyncio.timeout(max(0.001, loop["deadline"] - time.time())):
                result = await invoke(
                    call["tool"],
                    self.dispatch_arguments(call["arguments"]),
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
            if result.get("unavailable") or result.get("status") == "unavailable" or result.get("available") is False:
                status = "unavailable"
            elif name in POLICY_READ_TOOLS and not result.get("results"):
                status = "empty"
            elif name == "memory_search" and result.get("candidateEvidence"):
                status = "needs_review"
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
            or previous.get("candidateEvidence")
        )
        if not useful_previous or status == "ok":
            loop["results"][name] = result
        emit("loop_observation", {"tool": name, "status": status, "calls": loop["calls"]})
        return loop
