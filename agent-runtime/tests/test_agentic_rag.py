"""Knowledge quality, bounded recovery and isolation in the real loop methods."""

import time
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest

from benchmarks.runtime_fixture import make_runtime

from psych_agent_runtime.agentic_rag import AgenticKnowledge, response_coverage
from psych_agent_runtime.config import Settings
from psych_agent_runtime.response_prompt import build_response_prompt
from psych_agent_runtime.schemas import Plan
from psych_agent_runtime.tool_controller import ToolController


def hit(key, content="散步和阅读可以帮助放松。"):
    return {"id": key, "document_id": key.split(":")[0], "content": content,
            "source": "https://www.nimh.nih.gov/health/topics/caring-for-your-mental-health",
            "category": "anxiety", "rank": 1, "relevanceScore": .6}


def verdict(status="sufficient", keep=None, missing=None, next_query=None):
    return {"status": status, "keep": [0] if keep is None else keep,
            "missing": missing or [], "nextQuery": next_query}


def runtime(answers, results, *, budget=4, enabled=True, category=None):
    r = make_runtime()
    r.settings = Settings(agentic_rag_enabled=enabled, executor_provider="rules",
                          executor_goal_provider="", agent_loop_refinement_enabled=True)
    r.tool_controller = ToolController(r.settings)
    r.ollama = SimpleNamespace(chat_json=AsyncMock(side_effect=answers))
    r.tools = SimpleNamespace(catalog=SimpleNamespace(refresh=AsyncMock()), call=AsyncMock(side_effect=results))
    events = []
    r._emit = lambda name, data: events.append((name, data))
    plan = Plan(intent="knowledge", allowed_tools=["search_knowledge"],
                required_tools=["search_knowledge"], max_tool_calls=budget)
    state = {"username": "alice", "message": "那晚上有什么办法？", "session_id": 7,
             "context": {"messages": [{"role": "user", "content": "我睡前总担心明天的工作。"}]},
             "plan": plan.model_dump(),
             "loop": r.tool_controller.start_loop(plan, "那晚上有什么办法？", {
                 "search_knowledge": {"query": "那晚上有什么办法？", "limit": 6, "category": category}})}
    return r, state, events


async def run(r, state):
    for _ in range(8):
        state.update(await r._loop_decide(state))
        if state["loop"]["next"]["tool"] in {"finish", "clarify"}:
            return state
        state.update(await r._loop_tool(state))
        state.update(await r._loop_observe(state))
    raise AssertionError("read loop failed to terminate")


@pytest.mark.asyncio
async def test_sufficient_sources_respond_after_one_read_with_contextual_query():
    source = hit("relax:1")
    r, state, events = runtime([{"query": "睡前担忧工作时的放松方法"}, verdict()],
                               [{"hits": [source], "metrics": {"cacheHit": True}}])
    result = await run(r, state)
    assert r.tools.call.await_args.args == ("search_knowledge", {
        "query": "睡前担忧工作时的放松方法", "limit": 6})
    assert result["citations"] == [source]
    assert result["loop"]["calls"] == 1
    assert result["rag_metrics"]["agenticRag"]["status"] == "sufficient"
    assert r.ollama.chat_json.await_count == 2
    assert events[-1][0] == "knowledge_progress"
    assert any(e[1].get("stage") == "complete" for e in events)
    assessment = next(data for name, data in events
                      if name == "knowledge_progress" and data["stage"] == "assess" and data["status"] == "ok")
    assert assessment["sources"] == [{"url": source["source"], "title": "National Institute of Mental Health (NIMH)"}]
    payload = r.ollama.chat_json.await_args_list[0].args[0][1]["content"]
    assert "睡前总担心" in payload and "alice" not in payload


@pytest.mark.asyncio
async def test_partial_evidence_gets_one_targeted_retry_and_merges_both_sources():
    a, b, unrelated = hit("a:1"), hit("b:1", "睡前用笔列下明天的任务，留待白天处理。"), hit("bad:1", "社交焦虑症状。")
    r, state, _ = runtime([
        {"query": "睡前担忧工作如何放松"},
        verdict("partial", [0], ["缺少处理睡前担忧的资料"], "睡前担忧任务记录方法"),
        verdict("sufficient", [0, 1]),
    ], [{"hits": [a, unrelated]}, {"hits": [b, a]}])
    result = await run(r, state)
    assert r.tools.call.await_count == 2
    assert r.tools.call.await_args_list[1].args[1]["query"] == "睡前担忧任务记录方法"
    assert [h["id"] for h in result["citations"]] == ["a:1", "b:1"]
    assert [h["rank"] for h in result["citations"]] == [1, 2]
    assert result["citations"][0]["content"] == a["content"]
    assert result["citations"][1]["source"] == b["source"]
    assert result["loop"]["trace"][1]["provider"] == "agentic_rag"
    assert r.ollama.chat_json.await_count == 3
    assert not result["loop"]["refinement_used"]  # no independent third retry


@pytest.mark.asyncio
async def test_two_empty_rounds_stop_honestly_even_when_grader_requests_more():
    answer = verdict("insufficient", [], ["没有直接的依据"], "其他放松方法")
    r, state, _ = runtime([{"query": "睡前担忧"}, answer,
                           verdict("insufficient", [], ["仍没有直接依据"], "第三个检索词")],
                          [{"hits": []}, {"hits": []}])
    result = await run(r, state)
    assert r.tools.call.await_count == 2
    assert result["citations"] == []
    assert result["loop"]["knowledge"]["stopReason"] == "round_limit"
    assert result["loop"]["knowledge"]["status"] == "insufficient"


@pytest.mark.asyncio
@pytest.mark.parametrize("budget,next_query,reason", [(1, "不同查询", "read_budget"),
                                                    (4, " SLEEP   HELP ", "duplicate_query")])
async def test_budget_and_equivalent_query_stop_retry(budget, next_query, reason):
    r, state, _ = runtime([{"query": "sleep help"},
        verdict("partial", [0], ["缺少方法依据"], next_query)], [{"hits": [hit("a")]}], budget=budget)
    result = await run(r, state)
    assert r.tools.call.await_count == 1
    assert result["loop"]["knowledge"]["stopReason"] == reason


@pytest.mark.asyncio
async def test_user_information_gap_clarifies_instead_of_searching_again():
    r, state, _ = runtime([{"query": "睡前不适"},
        verdict("needs_user_input", [], ["需要用户说明不适的具体表现"])], [{"hits": []}])
    result = await run(r, state)
    assert r.tools.call.await_count == 1
    prompt = build_response_prompt(result, Plan.model_validate(result["plan"]),
                                   background_recall=False, goal_read=False)
    assert "needs_user_input" in prompt and "具体表现" not in prompt


@pytest.mark.asyncio
@pytest.mark.parametrize("bad", [
    verdict(keep=[99]), verdict(keep=[-1]), verdict(keep=[True]), verdict(keep=[0, 0]),
    verdict(keep=[]), verdict(missing=["矛盾的缺口"]),
    {**verdict(), "content": "编造来源"}, {**verdict(), "keep": "0"},
    {**verdict(), "nextQuery": "偷偷继续检索"},
])
async def test_invalid_review_never_promotes_unchecked_sources(bad):
    r, state, _ = runtime([{"query": "放松"}, bad], [{"hits": [hit("unreviewed")]}])
    result = await run(r, state)
    assert result["citations"] == []
    assert result["loop"]["knowledge"]["status"] == "unavailable"
    assert r.tools.call.await_count == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", [TimeoutError(), httpx.ConnectError("offline"), RuntimeError("provider unavailable")])
async def test_review_failure_after_retry_preserves_only_previous_approved_sources(failure):
    r, state, _ = runtime([{"query": "放松"},
        verdict("partial", [0], ["缺少睡前担忧方法"], "处理睡前担忧"), failure],
        [{"hits": [hit("approved")]}, {"hits": [hit("unreviewed")]}])
    result = await run(r, state)
    assert [h["id"] for h in result["citations"]] == ["approved"]
    assert result["loop"]["knowledge"]["stopReason"] == "review_unavailable"


@pytest.mark.asyncio
async def test_retry_retrieval_outage_keeps_prior_evidence_and_records_limitation():
    r, state, _ = runtime([{"query": "放松"},
        verdict("partial", [0], ["缺少睡前担忧方法"], "处理睡前担忧")],
        [{"hits": [hit("approved")]}, {"unavailable": True}])
    result = await run(r, state)
    assert [h["id"] for h in result["citations"]] == ["approved"]
    assert result["rag_metrics"]["agenticRag"]["stopReason"] == "retrieval_unavailable"
    assert r.ollama.chat_json.await_count == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("plan", [{"query": "new", "category": None}, {"tool": "search_web"},
                                  {"query": "x" * 241}, TimeoutError()])
async def test_planner_cannot_change_scope_and_failure_retains_original_authorized_query(plan):
    r, state, _ = runtime([plan, verdict()], [{"hits": [hit("a")]}], category="sleep")
    result = await run(r, state)
    args = r.tools.call.await_args.args[1]
    assert args["category"] == "sleep" and args["query"] == state["message"]
    assert result["loop"]["knowledge"]["planStatus"] == "unavailable"


@pytest.mark.asyncio
async def test_disabled_feature_is_original_retrieval_without_extra_calls():
    r, state, events = runtime([], [{"hits": [hit("a")]}], enabled=False)
    result = await run(r, state)
    r.ollama.chat_json.assert_not_awaited()
    assert "knowledge" not in result["loop"]
    assert not any(e[0] == "knowledge_progress" for e in events)
    assert result["citations"][0]["id"] == "a"


@pytest.mark.asyncio
async def test_expired_loop_cannot_execute_pending_knowledge_retry():
    r, state, _ = runtime([{"query": "放松"},
        verdict("partial", [0], ["缺少依据"], "新查询")], [{"hits": [hit("a")]}])
    state.update(await r._loop_decide(state)); state.update(await r._loop_tool(state))
    state.update(await r._loop_observe(state))
    state["loop"]["deadline"] = time.time() - 1
    result = await run(r, state)
    assert r.tools.call.await_count == 1
    assert result["loop"]["knowledge"]["stopReason"] == "deadline_exhausted"
    assert "retryQuery" not in result["loop"]["knowledge"]


def test_whole_chunk_budget_deduplicates_without_slicing_negations():
    large = hit("large", "先前描述。" * 2400 + "但这种做法不适用。")
    pool = AgenticKnowledge.candidates([hit("a")], {"hits": [hit("a"), large, hit("b")]})
    assert [h["id"] for h in pool] == ["a", "b"]


@pytest.mark.parametrize("delivered,status", [(["a", "b"], "sufficient"), (["a"], "partial"), ([], "insufficient")])
def test_response_budget_can_only_reduce_coverage(delivered, status):
    knowledge = {"hits": [hit("a"), hit("b")], "status": "sufficient", "missing": []}
    coverage = response_coverage(knowledge, delivered)
    assert coverage["status"] == status
    assert coverage["deliveredChunks"] == len(delivered)
    assert knowledge["status"] == "sufficient" and knowledge["missing"] == []


@pytest.mark.asyncio
async def test_knowledge_supplement_cannot_take_budget_from_required_memory_read():
    r, state, _ = runtime([{"query": "放松"},
        verdict("partial", [0], ["缺少依据"], "新查询")],
        [{"hits": [hit("a")]}, {"status": "sufficient", "evidence": [{"content": "我喜欢散步"}]}], budget=2)
    state["loop"]["allowed"].append("memory_search")
    state["loop"]["required"].append("memory_search")
    state["loop"]["arguments"]["memory_search"] = {"username": "alice", "query": "偏好", "limit": 3}
    result = await run(r, state)
    assert [call.args[0] for call in r.tools.call.await_args_list] == ["search_knowledge", "memory_search"]
    assert result["loop"]["knowledge"]["rounds"] == 1
    assert result["loop"]["knowledge"]["stopReason"] == "read_budget"
    assert r.tools.call.await_args_list[1].args[1]["username"] == "alice"
