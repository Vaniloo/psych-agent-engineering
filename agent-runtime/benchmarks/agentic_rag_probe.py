"""Read-only real-corpus diagnostic; never writes conversations or memories.

Run as a module with the existing private service environment already loaded.
The selector is deliberately fixed to knowledge retrieval to isolate the RAG
change. Use gateway smoke tests separately to verify routing and SSE delivery.
"""

import argparse
import asyncio
import json
import time
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

from psych_agent_runtime.capabilities import CapabilityExecutor
from psych_agent_runtime.config import Settings
from unittest.mock import AsyncMock
from benchmarks.runtime_fixture import make_runtime
from psych_agent_runtime.ollama_client import OllamaClient
from psych_agent_runtime.rag import RagEngine
from psych_agent_runtime.response import ResponseComposer
from psych_agent_runtime.schemas import Plan
from psych_agent_runtime.token_budget import ContextBudgetManager
from psych_agent_runtime.tool_controller import ToolController


async def run(args):
    output = Path(args.out)
    output.mkdir(parents=True, exist_ok=False)
    fixture = json.loads(Path(args.cases).read_text())
    settings = replace(Settings(), agentic_rag_enabled=True, executor_provider="rules")
    generation = OllamaClient(settings)
    rag = RagEngine(settings, generation)
    executor = CapabilityExecutor(None, rag, settings=settings)
    controller = ToolController(settings)
    rows = []
    try:
        await rag.start()
        readiness = await rag.knowledge_info()
        if readiness.get("status") != "ready":
            raise RuntimeError("knowledge corpus is not ready")
        for case in fixture["cases"]:
            baseline = await rag.search_with_metrics(case["message"], limit=6, bypass_cache=True)
            events = []
            runtime = make_runtime()
            runtime.settings, runtime.ollama, runtime.tool_controller = settings, generation, controller
            runtime.tools = SimpleNamespace(catalog=SimpleNamespace(refresh=AsyncMock()), call=executor.execute)
            runtime._emit = lambda name, payload, events=events: events.append({"event": name, "data": payload}) if name != "token" else None
            plan = Plan(intent="knowledge", allowed_tools=["search_knowledge"],
                        required_tools=["search_knowledge"], max_tool_calls=4)
            state = {"username": "synthetic-rag-diagnostic", "session_id": 1,
                     "request_id": "rag-" + case["id"], "message": case["message"],
                     "context": {"username": "synthetic-rag-diagnostic", "sessionId": 1,
                                 "messages": case.get("history", [])},
                     "plan": plan.model_dump(),
                     "loop": controller.start_loop(plan, case["message"], {
                         "search_knowledge": {"query": case["message"], "limit": 6}})}
            started = time.perf_counter()
            for _ in range(8):
                state.update(await runtime._loop_decide(state))
                if state["loop"]["next"]["tool"] in {"finish", "clarify"}:
                    break
                state.update(await runtime._loop_tool(state))
                state.update(await runtime._loop_observe(state))
            else:
                raise RuntimeError("knowledge loop failed to terminate")
            elapsed = time.perf_counter() - started
            answer = {}
            if args.generate:
                answer = await ResponseComposer(settings, generation,
                    ContextBudgetManager(settings.context_window_tokens, settings.response_reserve_tokens),
                    runtime._emit).generate(state)
            knowledge = state["loop"]["knowledge"]
            row = {**case, "baseline": baseline.model_dump(by_alias=True),
                   "agentic": {k: knowledge[k] for k in (
                       "policy", "rounds", "queries", "status", "hits", "missing", "retained", "stopReason", "planStatus")},
                   "readLoopSeconds": round(elapsed, 3), "events": events,
                   "reply": answer.get("reply"), "responseCoverage": answer.get("context_stats", {}).get("knowledgeEvidence")}
            rows.append(row)
            (output / "results.json").write_text(json.dumps({"scope": fixture["scope"],
                "selector": "rules-forced-knowledge-read", "corpus": readiness,
                "generationModel": settings.generation_model, "cases": rows}, ensure_ascii=False, indent=2))
            print(json.dumps({"id": case["id"], "rounds": knowledge["rounds"], "status": knowledge["status"],
                              "retained": knowledge["retained"], "seconds": round(elapsed, 3)}, ensure_ascii=False), flush=True)
            # Stop a broken generation provider rather than repeating failed API calls.
            if knowledge["status"] == "unavailable" and knowledge["planStatus"] == "unavailable":
                raise RuntimeError("query planning and evidence review unavailable; diagnostic stopped")
    finally:
        await controller.close()
        await rag.close()
        await generation.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cases", default="evaluation/agentic-rag-cases.json")
    parser.add_argument("--out", required=True)
    parser.add_argument("--generate", action="store_true")
    asyncio.run(run(parser.parse_args()))
