"""Budgeted evidence composition and streaming; no routing or business writes."""

from __future__ import annotations

import logging
import re
from typing import Any

from .config import Settings
from .goal_creation import explicit_creation
from .response_context import bounded_tool_context
from .response_prompt import build_response_prompt
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
        citations = [RagHit.model_validate(item) for item in state.get("citations", [])]
        plan = Plan.model_validate(state.get("plan") or {})
        quoted_definition = bool(
            re.search(r"解释(?:一下)?这句话|这句话.*什么意思", state["message"])
        )
        creation_request = explicit_creation(state["message"])
        acceptance = state.get("turn_acceptance") or {}
        if acceptance.get("completed"):
            reply = acceptance["assistantReply"]
            self._emit("token", {"text": reply})
            return {"reply": reply, "context_stats": {"mode": "completed_request_replay"}}
        if (
            creation_request
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
                reply = "这次还没有确认目标保存成功。你可以补充希望达成的具体目标；若刚才已经说清楚，也可以稍后重试。"
            self._emit("token", {"text": reply})
            return {"reply": reply, "context_stats": {"mode": "goal_operation_result"}}
        goal_intent = state.get("goal_intent") or {}
        if creation_request and goal_intent.get("intent") == "clarify":
            reply = goal_intent["question"]
            self._emit("token", {"text": reply})
            return {"reply": reply, "context_stats": {"mode": "goal_clarification"}}
        creation_request = creation_request and goal_intent.get("intent") != "none"
        background_recall = (state.get("tool_results", {}).get("memory_search") or {}).get(
            "purpose"
        ) == "context"
        goal_read = "get_goal_context" in state.get("tool_results", {})
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
        ):
            # Use the fresh tool sources for factual reads. Earlier assistant
            # mistakes in history/derived summaries must not become evidence.
            context = AgentContext(
                username=context.username,
                sessionId=context.session_id,
                rolePrompt=context.role_prompt,
                messages=[]
                if public_definition
                or quoted_definition
                or creation_request
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
        tool_text, truncated_tools = bounded_tool_context(
            non_rag_tool_results, self.context_budget.estimator
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
            extra_context_tokens=self.context_budget.estimator.count(tool_text),
        )
        context_stats = self.context_budget.debug_summary(budgeted)
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
            state, plan, background_recall=background_recall, goal_read=goal_read
        )
        messages = [
            {"role": "system", "content": system_prompt},
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

        reply_parts: list[str] = []
        try:
            async for token in self.generation.stream_chat(messages, purpose="response"):
                reply_parts.append(token)
                self._emit("token", {"text": token})
        except Exception:
            log.exception("Streaming model response failed")
            if reply_parts:
                # Never append a second reply to a partially emitted answer.
                raise
            reply_parts = [
                "我暂时无法连接到模型服务。你可以稍后再试；如果现在很难受，也可以先告诉我最困扰你的那一部分。"
            ]
            self._emit("token", {"text": reply_parts[0]})

        reply = "".join(reply_parts).strip()
        rag_trace = self._rag_trace(state, budgeted.citations)
        return {
            "reply": reply,
            "rag_trace": rag_trace,
            "context_stats": context_stats,
        }

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
