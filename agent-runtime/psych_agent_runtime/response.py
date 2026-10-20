"""Budgeted evidence composition and streaming; no routing or business writes."""

from __future__ import annotations

import logging
import re
from typing import Any

from .config import Settings
from .goal_model_gate import active_decision, wants_create
from .response_context import bounded_tool_context
from .response_prompt import build_response_prompt
from .response_protocol import InternalToolProtocol, TextProtocolGuard, contains_protocol
from .role_voice import build_voice
from .route_policy import explicit_knowledge_request, explicit_memory_request
from .schemas import AgentContext, Plan, RagHit, RagTrace
from .token_budget import ContextBudgetManager

log = logging.getLogger(__name__)


class ResponseComposer:
    def __init__(self, settings: Settings, generation, budget: ContextBudgetManager | None, emit):
        self.settings = settings
        self.generation = generation
        self.context_budget = budget
        self._emit = emit

    async def generate(self, state: dict) -> dict:
        self._emit("status", {"node": "respond", "label": "生成并流式输出回复"})
        context = AgentContext.model_validate(state["context"])
        # Previously leaked assistant protocol is neither valid dialogue nor tool evidence.
        context = context.model_copy(update={"messages": [m for m in context.messages
            if m.role != "assistant" or not contains_protocol(m.content)]})
        voice = build_voice(context, state["message"], self.context_budget.estimator) if getattr(self, "context_budget", None) else None
        citations = [RagHit.model_validate(item) for item in state.get("citations", [])]
        plan = Plan.model_validate(state.get("plan") or {})
        quoted_definition = bool(
            re.search(r"解释(?:一下)?这句话|这句话.*什么意思", state["message"])
        )
        creation_request = wants_create(state)
        gate = active_decision(state)
        acceptance = state.get("turn_acceptance") or {}
        if acceptance.get("completed"):
            reply = acceptance["assistantReply"]
            self._emit("token", {"text": reply})
            return {"reply": reply, "context_stats": {"mode": "completed_request_replay"}}
        management = (state.get("tool_results") or {}).get("manage_goal")
        user_web_search = (state.get("tool_results") or {}).get("search_web")
        if management is not None and not user_web_search:
            if management.get("applied") is True:
                title = management["goal"]["title"]
                operation = management["operation"]
                if operation == "revise":
                    steps = "\n".join(f"第{step['position']}步：{step['title']}" for step in management.get("changedSteps", []))
                    reply = f"已调整「{title}」的步骤并保存。" + ("\n\n" + steps if steps else "")
                elif operation == "complete":
                    reply = f"「{title}」已按你的决定标记为完成。这个目标先收在这里，后续不再自动追加步骤。"
                else:
                    reply = f"「{title}」已暂缓，后续不再自动追加步骤。想继续时可以再调整。"
            else:
                reply = "这次目标调整没有保存成功，可以稍后重试；当前目标保持原来的安排。"
            self._emit("token", {"text": reply})
            return {"reply": reply, "context_stats": {"mode": "goal_management_result"}}
        if gate and gate.get("operation") == "clarify":
            reply = "你这次指的是哪个目标里的哪一个小步骤？我先不改进度，确认清楚后再同步。"
            self._emit("token", {"text": reply})
            return {"reply": reply, "context_stats": {"mode": "goal_model_clarification"}}
        if (
            creation_request
            and not user_web_search
            and state.get("goal_operations_done")
            and (state.get("goal_intent") or {}).get("intent") != "clarify"
            and not re.search(r"总结|解释|介绍|回顾|查询|分析|什么是|告诉我", state["message"])
        ):
            goals = state.get("created_goals") or []
            if goals:
                goal = goals[0]
                steps = "\n".join(
                    f"{i + 1}. {a['title']}" for i, a in enumerate(goal.get("actions", []))
                )
                reply = f"已为你创建目标「{goal['title']}」。"
                if steps:
                    reply += "\n\n可以从这些小步骤开始：\n" + steps
                if goal.get("completionCriteria"):
                    reply += "\n\n完成标准：" + goal["completionCriteria"]
                reply += (
                    "\n\n目标已经保存到‘我的目标’，可以随时调整步骤，也可以在聊天里告诉我进展。"
                )
            else:
                reason = ((state.get("tool_results") or {}).get("create_goal") or {}).get("reason")
                detail = {
                    "decision_unavailable": "目标服务暂时不可用。这次目标尚未创建；你已经说清楚了，可以稍后重试，无需补充目标内容。",
                    "create_unavailable": "创建服务暂时不可用。这次还没有确认目标保存成功，可以稍后重试。",
                    "plan_unavailable": "目标步骤暂时生成失败。这次目标尚未保存，可以稍后重试。",
                    "invalid_plan": "这次生成的目标步骤不完整，尚未保存，可以稍后重试。",
                    "intent_unconfirmed": "本次创建确认未通过，目标尚未保存。可以稍后再试。",
                    "source_goal_not_found": "这次没有读到作为依据的原目标，后续目标尚未创建。可以稍后重试。",
                }
                reply = detail.get(reason, "这次还没有确认目标保存成功，可以稍后重试。")
            self._emit("token", {"text": reply})
            return {"reply": reply, "context_stats": {"mode": "goal_operation_result"}}
        goal_intent = state.get("goal_intent") or {}
        if creation_request and goal_intent.get("intent") == "clarify":
            reply = (
                "目标解析服务暂时不可用，目标尚未创建。可以稍后重试，无需重复说明目标内容。"
                if goal_intent.get("unavailable") else goal_intent["question"]
            )
            self._emit("token", {"text": reply})
            return {"reply": reply, "context_stats": {"mode": "goal_clarification"}}
        creation_request = creation_request and goal_intent.get("intent") != "none"
        background_recall = (state.get("tool_results", {}).get("memory_search") or {}).get(
            "purpose"
        ) == "context"
        goal_read = "get_goal_context" in state.get("tool_results", {})
        confirmed_progress = (state.get("tool_results", {}).get("sync_goal_progress") or {}).get("applied") is True
        public_definition = (
            plan.intent == "knowledge"
            and explicit_knowledge_request(state["message"])
            and not any(t in plan.required_tools for t in ("memory_search", "get_goal_context"))
            and not re.search(
                r"结合|针对|是否适合我|我的(?:情况|症状|经历)|我(?:最近|感到|觉得)",
                state["message"],
            )
        )
        if (
            plan.intent in {"memory", "dashboard"}
            or public_definition
            or ("memory_search" in state.get("tool_results", {}) and not background_recall)
            or quoted_definition
            or creation_request
            or goal_read
            or confirmed_progress
        ):
            # Use the fresh tool sources for factual reads. Earlier assistant
            # mistakes in history/derived summaries must not become evidence.
            context = AgentContext(
                username=context.username,
                sessionId=context.session_id,
                rolePrompt=context.role_prompt,
                roleConfiguration=context.role_configuration,
                messages=[]
                if public_definition
                or quoted_definition
                or creation_request
                or confirmed_progress
                or explicit_memory_request(state["message"])
                else [m for m in context.messages if m.role == "user"][-4:],
            )
        primary = (
            "get_support_resources"
            if state.get("support_level") == "professional_support"
            else {
                "memory": "memory_search",
                "dashboard": "get_dashboard",
                "strategy": "get_goal_context",
            }.get(plan.intent)
        )
        non_rag_tool_results = {
            name: value
            for name, value in (state.get("tool_results") or {}).items()
            if name != "search_knowledge"
        }
        if plan.routing.get("personalRecall") and "memory_search" in non_rag_tool_results:
            memory_result = dict(non_rag_tool_results["memory_search"])
            records = []
            show_time = bool(re.search(r"几点|具体时间|时刻|什么时候", state["message"]))
            for rank, source in enumerate(memory_result.get("evidence", []), 1):
                source = {**source, "recencyRank": rank}
                if not show_time and source.get("observedAt"):
                    source["observationDate"] = source.pop("observedAt")[:10]
                records.append(source)
            memory_result["evidence"] = records
            non_rag_tool_results["memory_search"] = memory_result
        # A large secondary goal result must not push requested memory evidence out of the budget.
        non_rag_tool_results = dict(
            sorted(non_rag_tool_results.items(), key=lambda pair: pair[0] != primary)
        )
        evidence_audit = {}
        tool_text, truncated_tools = bounded_tool_context(
            non_rag_tool_results, self.context_budget.estimator, audit=evidence_audit
        )
        if truncated_tools:
            tool_text += "\n[后续工具数据已截断，未显示的数据不能视为不存在。]"
        available_tokens = max(
            256,
            self.settings.context_window_tokens - self.settings.response_reserve_tokens,
        )
        prompt_user_message = self.context_budget.estimator.truncate(
            state["message"],
            max(256, available_tokens // 2),
        )
        budgeted = self.context_budget.build(
            context,
            prompt_user_message,
            citations,
            extra_context_tokens=self.context_budget.estimator.count(tool_text) + (voice.tokens if voice else 0),
        )
        context_stats = self.context_budget.debug_summary(budgeted)
        prompt_state = state
        if knowledge := (state.get("loop") or {}).get("knowledge"):
            from .agentic_rag import response_coverage
            coverage = response_coverage(knowledge, [h.id for h in budgeted.citations])
            context_stats["knowledgeEvidence"] = coverage
            prompt_state = {**state, "loop": {**state["loop"], "knowledge": {**knowledge, **coverage}}}
        context_stats["toolEvidence"] = evidence_audit
        if voice and voice.prompt:
            context_stats["roleVoice"] = {"enabled": True, "estimatedTokens": voice.tokens,
                "worldEntries": list(voice.selected), "worldExcluded": voice.excluded, "webReferences": voice.references}
        context_stats["inputTruncated"] = prompt_user_message != state["message"]
        self._emit("context", context_stats)

        citation_text = (
            "\n\n".join(
                f"[{item.rank}] 来源={item.source} 分类={item.category} "
                f"相关度={item.relevance_score:.2f}\n{item.content}"
                for item in budgeted.citations
            )
            or "本轮没有检索到足够相关的知识。"
        )
        system_prompt = build_response_prompt(
            prompt_state, plan, background_recall=background_recall, goal_read=goal_read
        )
        messages = [
            {"role": "system", "content": system_prompt},
            *([{"role": "system", "content": voice.prompt}] if voice and voice.prompt else []),
            {
                "role": "system",
                "content": (
                    f"<untrusted_memory_data>\n{budgeted.system_context}\n</untrusted_memory_data>"
                ),
            },
            *budgeted.history,
            {
                "role": "system",
                "content": (
                    "<untrusted_tool_results>\n"
                    f"{tool_text}\n"
                    "</untrusted_tool_results>\n"
                    "<untrusted_knowledge>\n"
                    f"{citation_text}\n"
                    "</untrusted_knowledge>"
                ),
            },
            {"role": "user", "content": prompt_user_message},
        ]

        reply = await self._stream_visible_reply(messages, context_stats)
        rag_trace = self._rag_trace(state, budgeted.citations)
        return {
            "reply": reply,
            "rag_trace": rag_trace,
            "context_stats": context_stats,
        }

    async def _stream_visible_reply(self, messages, context_stats):
        for attempt in range(2):
            reply_parts = []
            guard = TextProtocolGuard()
            try:
                async for token in self.generation.stream_chat(messages, purpose="response"):
                    safe = guard.feed(token)
                    if safe:
                        reply_parts.append(safe)
                        self._emit("token", {"text": safe})
                safe = guard.finish()
                if safe:
                    reply_parts.append(safe)
                    self._emit("token", {"text": safe})
                if attempt:
                    context_stats["responseGuard"]["repaired"] = True
                return "".join(reply_parts).strip()
            except InternalToolProtocol:
                log.warning("Blocked internal tool protocol in final response")
                context_stats["responseGuard"] = {"protocolBlocked": True, "repaired": False}
                if reply_parts:
                    self._emit("reply_reset", {"reason": "invalid_response"})
                if not attempt:
                    self._emit("status", {"node": "respond", "label": "重新整理回复"})
                    messages = [*messages, {"role": "system", "content":
                        "上一轮输出混入了不可展示的内部调用格式，已被丢弃且没有执行其中任何工具。"
                        "请重新直接回答用户；本阶段只允许自然语言，绝不输出调用协议、XML工具请求或代码式调用。"
                        "只使用已有真实工具结果，不能虚构天气或实时信息；缺少结果则如实说明，缺少城市则自然询问。"}]
                    continue
                reply = "这次没能整理出可靠的回复，可以稍后重试；尚未确认的信息不会当作查询结果。"
                self._emit("token", {"text": reply})
                return reply
            except Exception:
                log.exception("Streaming model response failed")
                if reply_parts:
                    # Preserve the existing failure semantics for a real interrupted stream.
                    raise
                reply = "我暂时无法连接到回复服务，可以稍后再试。这次还没有生成有效回答。"
                self._emit("token", {"text": reply})
                return reply

    def _rag_trace(self, state: dict, citations: list[RagHit]) -> dict[str, Any] | None:
        if not citations:
            return None
        trace = RagTrace(
            requestId=state["request_id"],
            query=state["message"],
            citationCount=len(citations),
            citations=[
                {
                    "rank": item.rank,
                    "id": item.id,
                    "category": item.category,
                    "source": item.source,
                    "relevanceScore": item.relevance_score,
                    "confidenceLabel": item.confidence_label,
                    "matchReason": item.match_reason,
                    "excerpt": item.content[:220],
                }
                for item in citations
            ],
            retrievalMetrics=state.get("rag_metrics") or {},
        )
        return trace.model_dump(by_alias=True, mode="json")
