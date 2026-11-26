"""The agent graph: one function-calling loop with host-side guardrails.

    load_context -> guard -+-> crisis ---------------------------+
                           +-> prepare -> agent <-> tools        |
                                            |                    |
                                            +-> persist <--------+-> metrics

- guard: deterministic safety scan + model risk review -> ordinary / professional / crisis.
- agent: ToolACE prefilters the tool list, then the main model streams a reply or
  requests tool calls (native function calling). The model writes its own queries.
- tools: the host validates arguments, binds identity, enforces the call budget,
  deadline and de-duplication, runs calls in parallel and feeds results back.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import Awaitable, Callable
from contextlib import asynccontextmanager
from typing import Any, TypedDict

from langgraph.config import get_stream_writer
from langgraph.graph import END, START, StateGraph

from .backend_client import BackendClient
from .config import Settings
from .guard import RiskDecision, assess
from .llm import ChatModel, ModelUnavailable
from .mcp_client import McpToolClient
from .memory import MemoryService
from .memory_writer import MemoryWriter
from .observability import record_agent_run
from .ollama_client import OllamaClient
from .prefilter import ToolPrefilter
from .prompts import system_prompt
from .rag import RagEngine
from .response_protocol import InternalToolProtocol, TextProtocolGuard
from .schemas import AgentContext, AgentRequest, AgentResponse, RagHit, RagTrace
from .telemetry import current_run, end_run, start_run
from .token_budget import ContextBudgetManager
from .tools import ALL_TOOLS, SUPPORT_RESOURCES, ToolError, Turn, available_tools, parse_arguments, run_tool

log = logging.getLogger(__name__)

SUPPORT_LABELS = {"ordinary": "ordinary", "professional": "professional_support", "crisis": "crisis"}
UNAVAILABLE_REPLY = "我暂时无法连接到回复服务，可以稍后再试。这次还没有生成有效回答。"


class AgentState(TypedDict, total=False):
    username: str
    session_id: int
    message: str
    request_id: str
    context: dict[str, Any]
    risk: dict[str, Any]
    loop: dict[str, Any]
    reply: str
    crisis: bool
    resources: list[dict[str, Any]]
    metrics: dict[str, Any]
    persistence_status: str


class AgentGraphRuntime:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.ollama = OllamaClient(settings)  # embeddings for knowledge retrieval
        self.backend = BackendClient(settings)
        self.rag = RagEngine(settings, self.ollama)
        self.model = self._create_model(settings)
        self.memory = MemoryService(settings, self.backend, MemoryWriter(self.model, self.backend),
                                    embedder=self.ollama)
        self.tools = McpToolClient(settings, self.backend, self.rag, self.memory, emit=self._emit)
        self.prefilter = ToolPrefilter(settings)
        self.context_budget = ContextBudgetManager(settings.context_window_tokens,
                                                   settings.response_reserve_tokens)
        self.graph: Any = None
        self._checkpointer_context: Any = None

    @staticmethod
    def _create_model(settings):
        try:
            return ChatModel.from_settings(settings)
        except ValueError:
            log.warning("No chat model configured; replies will report the outage")
            return None

    # ------------------------------------------------------------------ lifecycle

    async def start(self) -> None:
        await self.rag.start()
        await self.memory.start()
        self.graph = self._build_graph().compile(checkpointer=await self._create_checkpointer())

    async def close(self) -> None:
        if self.model is not None:
            await self.model.close()
        await self.prefilter.close()
        await self.memory.close()
        await self.rag.close()
        await self.backend.close()
        await self.ollama.close()
        if self._checkpointer_context is not None:
            await self._checkpointer_context.__aexit__(None, None, None)

    @asynccontextmanager
    async def _run(self, request: AgentRequest):
        if self.graph is None:
            raise RuntimeError("Agent graph has not been started")
        telemetry, token = start_run(self.settings, request.request_id)
        try:
            yield {"recursion_limit": 64, "configurable": {
                "thread_id": f"{request.username}:{request.session_id}:{request.request_id}"}}
        except Exception:
            if not telemetry.finalized:
                record_agent_run(telemetry.snapshot(status="error", persistence_status="failed"))
                telemetry.finalized = True
            raise
        finally:
            end_run(token)

    async def invoke(self, request: AgentRequest) -> AgentResponse:
        async with self._run(request) as config:
            return self.to_response(await self.graph.ainvoke(self._initial_state(request), config=config))

    async def stream(self, request: AgentRequest):
        async with self._run(request) as config:
            async for part in self.graph.astream(self._initial_state(request), config=config,
                                                 stream_mode=["custom", "updates", "values"], version="v2"):
                yield part

    def to_response(self, state: AgentState) -> AgentResponse:
        loop = state.get("loop") or {}
        names = list(dict.fromkeys(o["name"] for o in loop.get("observations", [])))
        return AgentResponse(
            reply=state.get("reply", ""), usedTool=bool(names), toolName=",".join(names) or None,
            sessionId=state["session_id"], crisis=state.get("crisis", False),
            helpCenterUrl="/help/crisis" if state.get("crisis") else None,
            resources=state.get("resources", []),
            ragTrace=self._rag_trace(state), metrics=state.get("metrics") or None,
        )

    # ------------------------------------------------------------------ graph

    def _build_graph(self) -> StateGraph:
        graph = StateGraph(AgentState)
        for name, handler in {
            "load_context": self._load_context, "guard": self._guard, "crisis": self._crisis,
            "prepare": self._prepare, "agent": self._agent, "tools": self._tools,
            "persist": self._persist,
        }.items():
            graph.add_node(name, self._timed(name, handler))
        graph.add_node("metrics", self._metrics)
        graph.add_edge(START, "load_context")
        graph.add_edge("load_context", "guard")
        graph.add_conditional_edges("guard", lambda s: "crisis" if s.get("crisis") else "prepare",
                                    {"crisis": "crisis", "prepare": "prepare"})
        graph.add_edge("prepare", "agent")
        graph.add_conditional_edges("agent", lambda s: "tools" if s["loop"]["pending"] else "persist",
                                    {"tools": "tools", "persist": "persist"})
        graph.add_edge("tools", "agent")
        graph.add_edge("crisis", "persist")
        graph.add_edge("persist", "metrics")
        graph.add_edge("metrics", END)
        return graph

    async def _load_context(self, state: AgentState) -> AgentState:
        self._emit("status", {"node": "load_context", "label": "加载会话与长期记忆"})
        return {"context": await self.tools.call(
            "get_user_context", {"username": state["username"], "session_id": state["session_id"]})}

    async def _guard(self, state: AgentState) -> AgentState:
        self._emit("status", {"node": "guard", "label": "安全检查"})
        history = (state.get("context") or {}).get("messages", [])
        decision: RiskDecision = await assess(state["message"], history, self.model)
        if decision.provider == "rules" and decision.reason == "model_unavailable" and current_run():
            current_run().fallback_count += 1
        self._emit("decision", {**decision.as_dict(), "supportLevel": SUPPORT_LABELS[decision.level]})
        return {"risk": decision.as_dict(), "crisis": decision.level == "crisis"}

    async def _crisis(self, state: AgentState) -> AgentState:
        self._emit("status", {"node": "crisis", "label": "切换危机支持流程"})
        result = await self.tools.call("crisis_support",
                                       {"username": state["username"], "message": state["message"]})
        reply = str(result.get("reply", ""))
        self._emit("token", {"text": reply})
        return {"reply": reply, "crisis": True, "resources": result.get("resources") or [],
                "loop": {"observations": [], "trace": [], "calls": 0, "pending": []}}

    async def _prepare(self, state: AgentState) -> AgentState:
        """Build the conversation for the model and apply host-required reads."""
        context = AgentContext.model_validate(state["context"])
        budgeted = self.context_budget.build(context, state["message"], [])
        level = state["risk"]["supportLevel"]
        messages = [{"role": "system", "content": system_prompt(
                        level, budgeted.system_context, self.settings.memory_query_timezone)},
                    *budgeted.history, {"role": "user", "content": state["message"]}]
        loop = {"messages": messages, "observations": [], "trace": [], "calls": 0, "step": 0,
                "pending": [], "offered": [], "seen": [], "resources": [],
                "tools": [t.name for t in available_tools(self.settings, state["username"])],
                "deadline": time.time() + max(1, self.settings.agent_loop_timeout_seconds)}
        if level == "professional":
            # Required read: the host calls it so the model always has vetted resources to cite.
            call = {"id": "host_support_resources", "name": SUPPORT_RESOURCES.name, "arguments": "{}"}
            loop["messages"].append({"role": "assistant", "content": None, "tool_calls": [
                {"id": call["id"], "type": "function",
                 "function": {"name": call["name"], "arguments": "{}"}}]})
            loop = await self._execute(loop, [call], state, allowed={SUPPORT_RESOURCES.name})
        return {"loop": loop, "resources": loop["resources"]}

    async def _agent(self, state: AgentState) -> AgentState:
        loop = {**state["loop"], "step": state["loop"]["step"] + 1}
        tools = [ALL_TOOLS[n] for n in loop["tools"]]
        budget_left = self._budget() - loop["calls"]
        # Steps are capped separately: invalid calls spend no budget but must not loop forever.
        stop = ("budget_exhausted" if budget_left <= 0 else
                "deadline_exhausted" if time.time() >= loop["deadline"] else
                "step_limit" if loop["step"] > self._budget() + 1 else "")
        if stop:
            offered, trace = [], {"step": loop["step"], "action": "assistant_text",
                                  "provider": "policy", "fallback": stop}
        else:
            self._emit("status", {"node": "agent", "label": "思考下一步"})
            choice = await self.prefilter.select(
                tools, history=(state.get("context") or {}).get("messages", []),
                request=state["message"], observations=loop["observations"],
                required={SUPPORT_RESOURCES.name} if state["risk"]["supportLevel"] == "professional"
                and SUPPORT_RESOURCES.name not in {o["name"] for o in loop["observations"]} else set())
            if choice.provider == "fallback" and current_run():
                current_run().fallback_count += 1
            offered, trace = choice.offered, choice.trace(loop["step"])
        loop["trace"] = [*loop["trace"], trace]
        loop["offered"] = [t.name for t in offered]
        self._emit("tool_decision", trace)

        visible, guard = [], TextProtocolGuard()

        def on_text(text):
            if safe := guard.feed(text):
                visible.append(safe)
                self._emit("token", {"text": safe})

        try:
            if self.model is None:
                raise ModelUnavailable("no chat model configured")
            step = await self.model.stream_step(loop["messages"],
                                                [t.openai_schema() for t in offered] or None, on_text)
            if tail := guard.finish():
                visible.append(tail)
                self._emit("token", {"text": tail})
        except InternalToolProtocol:
            log.warning("Blocked internal tool protocol in visible text")
            self._emit("reply_reset", {"reason": "invalid_response"})
            return {"loop": {**loop, "pending": []},
                    "reply": self._say("这次没能整理出可靠的回复，可以稍后重试。")}
        except Exception:  # noqa: BLE001 - an outage becomes an honest reply, not a crash
            log.exception("Agent model step failed")
            if visible:
                raise
            return {"loop": {**loop, "pending": []}, "reply": self._say(UNAVAILABLE_REPLY)}

        if step.tool_calls:
            if visible:
                # Text written before a tool call is a preamble; the real answer comes after.
                self._emit("reply_reset", {"reason": "tool_call"})
            loop["messages"] = [*loop["messages"], {
                "role": "assistant", "content": step.content or None,
                "tool_calls": [c.as_message() for c in step.tool_calls]}]
            loop["pending"] = [{"id": c.id, "name": c.name, "arguments": c.arguments}
                               for c in step.tool_calls]
            return {"loop": loop}
        reply = "".join(visible).strip()
        return {"loop": {**loop, "pending": []}, "reply": reply}

    async def _tools(self, state: AgentState) -> AgentState:
        self._emit("status", {"node": "tools", "label": "调用工具"})
        loop = state["loop"]
        loop = await self._execute(loop, loop["pending"], state, allowed=set(loop["offered"]))
        return {"loop": {**loop, "pending": []}, "resources": loop["resources"]}

    async def _execute(self, loop: dict, calls: list[dict], state: AgentState, *, allowed: set[str]) -> dict:
        """Validate and run tool calls in parallel. Every call gets exactly one tool message."""
        loop = {**loop, "messages": list(loop["messages"]), "observations": list(loop["observations"]),
                "seen": list(loop["seen"]), "resources": list(loop["resources"])}
        turn = Turn(state["username"], state["session_id"])
        budget_left = self._budget() - loop["calls"]
        runnable, replies = [], {}
        for call in calls:
            tool = ALL_TOOLS.get(call["name"])
            if tool is None or call["name"] not in allowed:
                replies[call["id"]] = {"error": "该工具本轮不可用"}
                continue
            try:
                args = parse_arguments(tool, call["arguments"])
            except ToolError as exc:
                replies[call["id"]] = {"error": f"参数无效：{exc}"}
                continue
            key = json.dumps([tool.name, args.model_dump()], sort_keys=True, ensure_ascii=False)
            if key in loop["seen"]:
                replies[call["id"]] = {"error": "相同的调用已经执行过，请直接使用之前的结果"}
                continue
            if len(runnable) >= budget_left:
                replies[call["id"]] = {"error": "本轮工具调用次数已用完，请根据已有信息回答"}
                continue
            used = sum(o["name"] == tool.name for o in loop["observations"]) + \
                sum(t.name == tool.name for _, t, _ in runnable)
            if used >= tool.max_calls:
                replies[call["id"]] = {"error": f"{tool.name} 本轮最多调用 {tool.max_calls} 次，请根据已有结果回答"}
                continue
            loop["seen"].append(key)
            runnable.append((call, tool, args))

        async def run(call, tool, args):
            remaining = max(0.001, loop["deadline"] - time.time())
            try:
                async with asyncio.timeout(remaining):
                    return await run_tool(tool, args, turn, self.tools.call)
            except TimeoutError:
                return "timeout", {"unavailable": True}

        results = await asyncio.gather(*(run(*item) for item in runnable))
        for (call, tool, args), (status, raw) in zip(runnable, results, strict=True):
            summary = tool.summarize(raw) if status == "ok" else {"error": "工具暂时不可用", "status": status}
            replies[call["id"]] = summary
            loop["observations"].append({"name": tool.name, "arguments": args.model_dump(),
                                         "status": status, "result": summary, "raw": raw})
            if tool.name == "search_knowledge" and status == "ok" and current_run():
                current_run().record_retrieval(raw.get("metrics") or {})
            if tool.name == SUPPORT_RESOURCES.name and status == "ok":
                loop["resources"] = raw
        loop["calls"] += len(runnable)
        for call in calls:
            loop["messages"].append({"role": "tool", "tool_call_id": call["id"],
                                     "content": json.dumps(replies[call["id"]], ensure_ascii=False)})
        self._emit("loop_observation", {"calls": loop["calls"],
                                        "tools": [c["name"] for c, _, _ in runnable]})
        return loop

    async def _persist(self, state: AgentState) -> AgentState:
        self._emit("status", {"node": "persist", "label": "保存对话并安排记忆更新"})
        try:
            await self.tools.call("persist_turn", {
                "username": state["username"], "session_id": state["session_id"],
                "user_message": state["message"], "assistant_reply": state.get("reply", ""),
                "compression": {}, "request_id": state["request_id"]})
            self._emit("memory", {"status": "pending", "label": "记忆将在后台更新"})
            return {"persistence_status": "saved"}
        except Exception:
            log.exception("Unable to persist agent turn")
            self._emit("warning", {"message": "回复已生成，但本轮记忆暂未保存"})
            return {"persistence_status": "failed"}

    async def _metrics(self, state: AgentState) -> AgentState:
        telemetry = current_run()
        if telemetry is None:
            return {}
        loop, risk = state.get("loop") or {}, state.get("risk") or {}
        observations = loop.get("observations", [])
        failed = [o["name"] for o in observations if o["status"] != "ok"]
        persistence = state.get("persistence_status", "unknown")
        memory = next((o for o in reversed(observations) if o["name"] == "recall_memory"), None)
        telemetry.pipeline = {
            "router": {"provider": risk.get("provider", "rules"), "route": "crisis" if state.get("crisis") else "agent",
                       "riskBand": risk.get("riskBand", "unknown"),
                       "supportLevel": SUPPORT_LABELS.get(risk.get("supportLevel"), "crisis"
                                                          if state.get("crisis") else "unknown")},
            "risk": risk,
            "executor": {"provider": self.settings.executor_provider, "role": "tool_prefilter",
                         "contract": "public-tool-next-action-v1", "decisions": loop.get("trace", [])},
            "loop": {"version": "function-calling-v1", "readCalls": loop.get("calls", 0),
                     "budget": self._budget(), "steps": loop.get("step", 0),
                     "tools": [{"name": o["name"], "arguments": o["arguments"], "status": o["status"]}
                               for o in observations]},
            "knowledge": self._knowledge_trace(observations),
            "memory": {"provider": "rules", "contract": "bm25-owned-index",
                       "delivery": "queued" if persistence == "saved" else "failed",
                       "retrieval": {"status": memory["status"],
                                     "evidenceCount": len(memory["result"].get("records", []))}
                       if memory else None},
        }
        metrics = telemetry.snapshot(
            status="success" if persistence == "saved" and not failed else "partial",
            persistence_status=persistence)
        record_agent_run(metrics)
        telemetry.finalized = True
        self._emit("metrics", metrics)
        return {"metrics": metrics}

    # ------------------------------------------------------------------ helpers

    def _budget(self) -> int:
        return min(16, max(1, self.settings.agent_loop_max_tool_calls))

    @staticmethod
    def _knowledge_trace(observations: list[dict]) -> dict:
        """How many knowledge rounds ran, with which queries, and how strong each result was."""
        rounds = [{"query": o["arguments"]["query"], "status": o["status"],
                   "evidence": o["result"].get("evidence", "none") if o["status"] == "ok" else "unavailable",
                   "hits": len(o["result"].get("hits", [])) if o["status"] == "ok" else 0}
                  for o in observations if o["name"] == "search_knowledge"]
        return {"rounds": len(rounds), "requeried": len(rounds) > 1, "calls": rounds,
                "finalEvidence": rounds[-1]["evidence"] if rounds else None}

    def _say(self, text: str) -> str:
        self._emit("token", {"text": text})
        return text

    def _rag_trace(self, state: AgentState) -> dict | None:
        hits = [RagHit.model_validate(h) for o in (state.get("loop") or {}).get("observations", [])
                if o["name"] == "search_knowledge" and o["status"] == "ok"
                for h in o["raw"].get("hits", [])]
        if not hits:
            return None
        return RagTrace(
            requestId=state["request_id"], query=state["message"], citationCount=len(hits),
            citations=[{"rank": h.rank, "id": h.id, "category": h.category, "source": h.source,
                        "relevanceScore": h.relevance_score, "confidenceLabel": h.confidence_label,
                        "matchReason": h.match_reason, "excerpt": h.content[:220]} for h in hits],
        ).model_dump(by_alias=True, mode="json")

    def _initial_state(self, request: AgentRequest) -> AgentState:
        return {"username": request.username, "session_id": request.session_id,
                "message": request.message, "request_id": request.request_id,
                "context": {}, "risk": {}, "reply": "", "crisis": False, "resources": [],
                "metrics": {}, "persistence_status": "unknown"}

    async def _create_checkpointer(self) -> Any:
        if self.settings.checkpoint_backend.lower() == "redis":
            try:
                from langgraph.checkpoint.redis.aio import AsyncRedisSaver

                self._checkpointer_context = AsyncRedisSaver.from_conn_string(self.settings.redis_url)
                checkpointer = await self._checkpointer_context.__aenter__()
                await checkpointer.asetup()
                return checkpointer
            except Exception:
                log.warning("Redis checkpointer unavailable; using in-memory saver", exc_info=True)
        from langgraph.checkpoint.memory import InMemorySaver

        return InMemorySaver()

    def _timed(self, name: str, node: Callable[[AgentState], Awaitable[AgentState]]):
        async def wrapped(state: AgentState) -> AgentState:
            started = time.perf_counter()
            try:
                return await node(state)
            finally:
                duration = (time.perf_counter() - started) * 1000
                if (telemetry := current_run()) is not None:
                    telemetry.record_node(name, duration)
                self._emit("node_metric", {"node": name, "durationMs": round(duration, 2)})
        return wrapped

    @staticmethod
    def _emit(event: str, data: dict[str, Any]) -> None:
        if event == "token" and (telemetry := current_run()) is not None:
            telemetry.mark_first_token()
        try:
            get_stream_writer()({"event": event, "data": data})
        except RuntimeError:
            pass

