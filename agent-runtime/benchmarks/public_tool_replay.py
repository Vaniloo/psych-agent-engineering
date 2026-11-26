"""Development-only real-registry/model and graph replay; never touches business stores.

Requests reuse existing regression/quality cases. Gold is project-authored, not an
independent benchmark. Generator and router fixtures are explicitly
separate from the real base/candidate tool scorer. Nothing here is training data.
"""

import argparse
import ast
import asyncio
import copy
import json
import time
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx

from psych_agent_runtime.capabilities import CAPABILITIES, normalize_result, validate_arguments
from psych_agent_runtime.config import Settings
from benchmarks.runtime_fixture import make_runtime
from psych_agent_runtime.route_policy import guarantee_requested_reads, plan_for_route
from psych_agent_runtime.schemas import AgentRequest
from psych_agent_runtime.token_budget import ContextBudgetManager
from psych_agent_runtime.tool_controller import ToolController
from training.public_tools.convert import digest
from training.public_tools.course import TEXT

READS = ["search_knowledge", "memory_search", "get_dashboard",
         "get_support_resources", "search_web"]
BOUND = {"username", "session_id", "request_id", "model_approval"}
OWNER = "public-replay-fixture-20261019"


def catalog():
    # Extract actual MCP descriptions without importing/starting the MCP server.
    path = Path(__file__).parents[1] / "psych_agent_runtime/mcp_server.py"
    descriptions = {n.name: ast.get_docstring(n) for n in ast.parse(path.read_text()).body
                    if isinstance(n, ast.AsyncFunctionDef)}
    result = {}
    for name in READS:
        original = CAPABILITIES[name].arguments.model_json_schema()
        schema = copy.deepcopy(original)
        for field in BOUND:
            schema.get("properties", {}).pop(field, None)
        schema["required"] = [f for f in schema.get("required", []) if f not in BOUND]
        result[name] = {"name": name, "description": descriptions[name], "parameters": schema}
    return result


CATALOG = catalog()


def model_row(message, names=READS, history=(), observations=()):
    context = [{"role": r, "content": c} for r, c in history]
    context.append({"role": "user", "content": message})
    for name, result in observations:
        context.extend([{"role": "tool_call", "content": json.dumps({"name": name, "arguments": {}}, ensure_ascii=False)},
                        {"role": "observation", "content": json.dumps(result, ensure_ascii=False)}])
    return {"context": context, "candidateTools": [{"kind": "tool", "tool": CATALOG[n]} for n in names] + [TEXT]}


def tool_fixture(name, message="", unavailable=False):
    if unavailable:
        return {"unavailable": True}
    if name == "memory_search":
        content = ("我最近天天吃肯德基，有点腻了。" if "吃" in message else
                   "我今天心情很好。" if "心情" in message else "我喜欢听轻音乐，也喜欢在安静的花园散步。")
        return {"status": "sufficient", "evidence": [{"id": "source-1", "content": content,
            "sessionId": 2, "observedAt": "2026-10-19T09:00:00+08:00", "timestampRole": "observation_time",
            "source": "isolated-fixture", "role": "user"}]}
    if name == "search_knowledge":
        return {"hits": [{"id": "knowledge-1", "document_id": "fixture-document", "content": "隔离知识检索结果。",
            "category": "psychology", "source": "existing-regression-fixture"}], "metrics": {"returned": 1}}
    if name == "get_dashboard":
        return {"reportSummaryResponse": [], "conversationOverview": {"status": "available", "summary": "隔离对话概览。"}}
    if name == "get_support_resources":
        return [{"name": "隔离测试资源", "type": "test", "contact": "test-only",
                 "description": "不提供真实联系方式", "urgent": False}]
    if name == "search_web":
        return {"available": True, "results": [{"title": "天气查询测试结果", "url": "https://example.org/weather",
            "snippet": "仅用于回放的网页结果，不是真实天气。"}]}
    raise ValueError("no_fixture_for_" + name)


def raw_cases():
    from benchmarks.chat_quality import CASES
    expected = {"memory_compound": ["memory_search"], "memory_reworded": ["memory_search"],
        "memory_breadth": ["memory_search"], "memory_missing": ["memory_search"],
        "memory_optout": ["search_knowledge"], "quoted_request": ["assistant_text"],
        "report": ["get_dashboard"],
        "mixed_report_memory": ["get_dashboard", "memory_search"], "chat": ["assistant_text"],
        "chat_no_inference": ["assistant_text"], "knowledge": ["search_knowledge"],
        "mixed_knowledge_memory": ["memory_search", "search_knowledge"]}
    rows, seen = [], set()
    for _, scenario, message in CASES:
        if scenario in expected and message not in seen:
            seen.add(message)
            rows.append({"id": "quality-" + scenario, "message": message, "expected": expected[scenario],
                         "source": "benchmarks/chat_quality.py", "group": "first_action"})
    for name, message, targets in [
        ("meal", "我最近经常吃什么", ["memory_search"]),
        ("mood", "我今天心情怎么样", ["memory_search"]),
        ("weather", "广州今天出门需要带伞吗", ["search_web"]),
        ("weather_missing_city", "今天天气怎么样", ["assistant_text"]),
        ("stable_knowledge", "什么是梯度下降", ["assistant_text"]),
        ("support", "我想找专业心理支持资源，有哪些渠道", ["get_support_resources"]),
        ("thanks", "谢谢你，今天先聊到这里", ["assistant_text"]),
    ]:
        rows.append({"id": name, "message": message, "expected": targets, "group": "first_action",
                     "source": "user_reported_regressions_or_tests/test_proactive_search.py"})
    rows.append({"id": "weather_followup", "message": "广州", "expected": ["search_web"],
        "history": [["user", "搜一下今天天气怎么样"], ["assistant", "你在哪个城市？"]],
        "group": "history", "source": "user_reported_search_followup"})
    for name, message, tool in [
        ("memory_answered", "我最近经常吃什么", "memory_search"),
        ("mood_answered", "我今天心情怎么样", "memory_search"),
        ("knowledge_answered", "什么是认知行为疗法", "search_knowledge"),
        ("report_answered", "请看看我的报告", "get_dashboard"),
        ("web_answered", "广州今天的天气怎么样", "search_web"),
    ]:
        rows.append({"id": name, "message": message, "expected": ["assistant_text"],
            "observations": [[tool, tool_fixture(tool, message)]], "group": "after_observation",
            "source": "existing_tool_result_shapes_and_user_regressions"})
    return rows


class Scores:
    def __init__(self, url):
        self.client = httpx.AsyncClient(base_url=url, timeout=90, trust_env=False)
        self.records = []

    async def score(self, row, mode):
        started = time.perf_counter()
        reply = await self.client.post("/score", json={"row": row, "mode": mode})
        reply.raise_for_status()
        result = reply.json()
        result["roundtripMs"] = (time.perf_counter() - started) * 1000
        return result


class Facade:
    def __init__(self, scores, mode):
        self.scores, self.mode, self.loop, self.records = scores, mode, None, []

    async def evaluate(self, state, tasks, **kwargs):
        if tasks != ["next_action"]:
            raise ValueError("only next_action is supported")
        loop = self.loop
        row = model_row(loop["request"], state["allowedTools"],
                        observations=list(loop["results"].items()))
        result = await self.scores.score(row, self.mode)
        index = max(range(len(result["scores"])), key=result["scores"].__getitem__)
        candidate = row["candidateTools"][index]
        # This ends the read sub-loop and delegates to ResponseComposer, never
        # claiming the task is finished or inventing a trained clarify category.
        choice = "finish" if candidate["kind"] == "assistant_text" else candidate["tool"]["name"]
        self.records.append({"row": row, "choice": choice, "rawAction": candidate["kind"], **result})
        return {"provider": "isolated_public_" + self.mode, "decisions": {"next_action": {
            "choice": choice, "accepted": True}}}  # uncalibrated argmax only in this probe

    async def close(self):
        pass


class ProbeController(ToolController):
    async def decide(self, loop, emit, refiner=None):
        self.model.loop = loop
        return await super().decide(loop, emit, refiner)


class GenerationFixture:
    def __init__(self, web=False):
        self.web = web

    async def chat_json(self, *args, purpose=None, **kwargs):
        if purpose == "web_search_gate" or self.web:
            return {"topic": "local_weather", "search": True, "query": "广州 今日 天气 降雨",
                "location": "广州", "locationQuote": "广州今天出门需要带伞吗", "fresh": True,
                "externalReason": "freshness", "publicQuery": True}
        return {"tool": "finish"}

    async def stream_chat(self, *args, **kwargs):
        yield "[隔离生成器：仅验证调度和数据流，不评估真实回答质量]"


async def graph_case(scores, mode, name, message, route, must, forbidden=(), web=False, unavailable=False,
                     read_gateway=None, model_factory=None):
    settings = Settings(memory_context_recall_enabled=False, agent_loop_refinement_enabled=False,
        executor_provider="rules", web_search_enabled=web,
        web_search_users=OWNER, web_search_proactive_enabled=web, agent_loop_timeout_seconds=120)
    runtime = make_runtime()
    runtime.settings = settings
    runtime.tool_controller = ProbeController(settings)
    facade = model_factory() if model_factory else Facade(scores, mode)
    if model_factory:
        runtime.settings = replace(settings, executor_provider="public_qwen")
    runtime.tool_controller.model = facade
    runtime.context_budget = ContextBudgetManager(8192, 1200)
    runtime.ollama = GenerationFixture(web)
    routing = {"conversationKind": "chat"}
    plan = guarantee_requested_reads(plan_for_route(route, message, "frozen-replay-route", routing), message)
    runtime.planner = SimpleNamespace(plan=AsyncMock(return_value=plan))
    events, calls = [], []
    runtime._emit = lambda event, data: events.append({"event": event, "data": data})

    async def invoke(tool, arguments):
        args = validate_arguments(tool, arguments)
        if "username" in args and args["username"] != OWNER:
            raise ValueError("cross_owner_arguments")
        calls.append({"tool": tool, "arguments": args})
        if read_gateway is not None and tool in {*READS, "risk_scan", "get_user_context"}:
            return await read_gateway(tool, args)
        if tool == "risk_scan":
            result = {"highRisk": False}
        elif tool == "get_user_context":
            result = {"username": OWNER, "sessionId": 9}
        elif tool == "persist_turn":
            result = {"saved": True}
        else:
            result = tool_fixture(tool, message, unavailable and tool in {"memory_search", "search_knowledge"})
        if isinstance(result, dict) and result.get("unavailable"):
            raise RuntimeError("isolated_fixture_tool_unavailable")
        return normalize_result(tool, result)

    runtime.tools = SimpleNamespace(call=invoke)
    runtime.graph = runtime._build_graph().compile()
    request = AgentRequest(username=OWNER, sessionId=9, message=message, requestId="probe-" + mode + "-" + name)
    started = time.perf_counter()
    try:
        state = await runtime.graph.ainvoke(runtime._initial_state(request), config={"recursion_limit": 64})
        names = [c["tool"] for c in calls]
        actual = set(names)
        success = set(must) <= actual and not set(forbidden) & actual and state["persistence_status"] == "saved"
        trace = state.get("loop", {}).get("trace", [])
        return {"id": name, "mode": mode, "passed": success, "calls": calls, "trace": trace,
            "modelDecisions": facade.records, "reply": state["reply"], "must": must, "forbidden": forbidden,
            "persistenceStatus": state["persistence_status"],
            "realBusinessWrites": False, "generation": "fixture_not_quality_evaluated",
            "router": "frozen_fixture_not_router_quality", "durationMs": (time.perf_counter() - started) * 1000}
    except Exception as exc:
        return {"id": name, "mode": mode, "passed": False, "error": type(exc).__name__ + ":" + str(exc)[:300], "calls": calls}
    finally:
        await runtime.tool_controller.close()


def graph_cases():
    return [
        dict(name="chat", message="今天事情忙完了，想轻松聊两句。", route="chat", must=[], forbidden=READS),
        dict(name="knowledge", message="什么是认知行为疗法？", route="knowledge", must=["search_knowledge"]),
        dict(name="meal_memory", message="我最近经常吃什么", route="chat", must=["memory_search"]),
        dict(name="mood_memory", message="我今天心情怎么样", route="chat", must=["memory_search"]),
        dict(name="report", message="请查看我的心理报告", route="chat", must=["get_dashboard"]),
        dict(name="optout", message="不要查我之前的记录，只用两句话解释什么是CBT。", route="knowledge",
             must=["search_knowledge"], forbidden=["memory_search"]),
        dict(name="compound", message="请查看我的心理报告，并回顾我以前喜欢听什么。",
             route="chat", must=["get_dashboard", "memory_search"]),
        dict(name="web", message="广州今天出门需要带伞吗", route="chat", must=["search_web"], web=True),
        dict(name="read_unavailable", message="我最近经常吃什么", route="chat", must=["memory_search"], unavailable=True),
        dict(name="optional_knowledge", message="给我一点压力应对方法", route="knowledge", must=["search_knowledge"]),
    ]


async def run(args):
    args.out.mkdir(parents=True, exist_ok=False)
    raw, graph = raw_cases(), graph_cases()
    fixture = {"rawCases": raw, "graphCases": graph, "catalog": CATALOG,
               "schemaProjection": "server_bound_identity_fields_removed_from_model_view",
               "scope": "project_authored_development_regression_not_training_or_independent_benchmark"}
    (args.out / "fixtures.json").write_text(json.dumps(fixture, ensure_ascii=False, indent=2))
    scores = Scores(args.url)
    results, graphs = [], []
    try:
        health = (await scores.client.get("/health")).json()
        (args.out / "model.json").write_text(json.dumps(health, indent=2))
        for mode in ("base", "candidate"):
            for case in raw:
                for order in ("original", "reversed"):
                    row = model_row(case["message"], history=case.get("history", ()), observations=case.get("observations", ()))
                    if order == "reversed":
                        row["candidateTools"] = list(reversed(row["candidateTools"]))
                    reply = await scores.score(row, mode)
                    i = max(range(len(reply["scores"])), key=reply["scores"].__getitem__)
                    candidate = row["candidateTools"][i]
                    chosen = "assistant_text" if candidate["kind"] == "assistant_text" else candidate["tool"]["name"]
                    result = {**case, "mode": mode, "order": order, "predicted": chosen,
                              "passed": chosen in case["expected"], **reply}
                    results.append(result)
                    with (args.out / "raw-results.jsonl").open("a") as f:
                        f.write(json.dumps(result, ensure_ascii=False) + "\n")
            for case in graph:
                result = await graph_case(scores, mode, **case)
                graphs.append(result)
                with (args.out / "graph-results.jsonl").open("a") as f:
                    f.write(json.dumps(result, ensure_ascii=False) + "\n")
                print(mode, case["name"], "pass" if result["passed"] else "FAIL", flush=True)
        summary = {"fixtureSha256": digest(fixture), "productionChanged": False, "teacherApiCalled": False,
                   "businessPersistence": "isolated_fixture_only", "modes": {}}
        for mode in ("base", "candidate"):
            rr = [r for r in results if r["mode"] == mode]
            gg = [r for r in graphs if r["mode"] == mode]
            summary["modes"][mode] = {"raw": {"rows": len(rr), "correct": sum(r["passed"] for r in rr)},
                "rawByGroup": {g: {"rows": sum(r["group"] == g for r in rr),
                                   "correct": sum(r["passed"] and r["group"] == g for r in rr)} for g in {r["group"] for r in rr}},
                "graph": {"rows": len(gg), "passed": sum(r["passed"] for r in gg),
                          "modelDecisions": sum(len(r.get("modelDecisions", [])) for r in gg)},
                "rawFailures": [{k: r[k] for k in ("id", "order", "predicted", "expected")} for r in rr if not r["passed"]]}
        (args.out / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2))
        print(json.dumps(summary, ensure_ascii=False))
    finally:
        await scores.client.aclose()


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--url", default="http://127.0.0.1:18119")
    p.add_argument("--out", type=Path, required=True)
    a = p.parse_args()
    asyncio.run(run(a))
