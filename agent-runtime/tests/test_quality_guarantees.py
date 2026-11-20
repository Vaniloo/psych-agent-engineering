import json
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from benchmarks.loop_probe import run_controller
from psych_agent_runtime.config import Settings
from psych_agent_runtime.memory import MemoryService, MemoryStore
from psych_agent_runtime.memory_query import lookup_query, query_facets, recall_only_question
from psych_agent_runtime.memory_source_guard import clearly_reverse_direction
from psych_agent_runtime.report_context import dashboard_with_availability
from psych_agent_runtime.response_context import bounded_tool_context
from psych_agent_runtime.route_policy import guarantee_requested_reads, plan_for_route
from psych_agent_runtime.token_budget import TokenEstimator
from psych_agent_runtime.tool_controller import ToolController


@pytest.mark.asyncio
@pytest.mark.parametrize("route", ["chat", "memory", "dashboard", "clarify", "knowledge"])
async def test_explicit_lookup_survives_bad_route_and_executor_finish(route):
    message = "我之前提到喜欢听什么、在哪里休息？"
    controller = ToolController(Settings(executor_provider="public_qwen"))
    controller.model.evaluate = AsyncMock(
        return_value={"decisions": {"next_action": {"choice": "finish", "accepted": True}}}
    )
    invoke = AsyncMock(return_value={"evidence": []})
    try:
        plan = guarantee_requested_reads(plan_for_route(route, message, "test"), message, None)
        assert "memory_search" in plan.required_tools
        results, trace = await run_controller(controller, plan, message, invoke, lambda *_: None)
        assert "memory_search" in results
        assert trace[0]["provider"] == "policy"
        assert invoke.await_count <= 2
        assert all("write" not in name for name in results)
    finally:
        await controller.close()


@pytest.mark.parametrize(
    "message",
    [
        "不要查我之前的记录，只解释什么是CBT。",
        "请解释这句话：‘你记得我喜欢什么吗？’",
        "朋友问我‘你记得我喜欢什么吗’，如何回复？",
    ],
)
def test_quotes_and_optout_remove_personal_memory_permission(message):
    plan = guarantee_requested_reads(plan_for_route("memory", message, "test"), message)
    assert "memory_search" not in plan.allowed_tools
    assert plan.intent != "memory"


def test_mixed_report_and_memory_keep_both_read_capabilities():
    message = "查看我的报告，再回顾我之前提到的休息偏好。"
    plan = guarantee_requested_reads(plan_for_route("dashboard", message, "test"), message)
    assert plan.intent == "dashboard"
    assert plan.required_tools == ["get_dashboard", "memory_search"]
    assert plan.max_tool_calls == 2


def test_mixed_lookup_query_keeps_only_the_marked_recall_task():
    question = "先告诉我有没有近期对话概览，再按以前的记录告诉我喜欢听什么。请简短回答。"
    assert lookup_query(question) == "按以前的记录告诉我喜欢听什么。请简短回答。"
    plan = guarantee_requested_reads(plan_for_route("chat", question, "test"), question)
    assert plan.required_tools == ["get_dashboard", "memory_search"]


def test_explicit_knowledge_and_memory_lookup_keep_both_resources():
    message = "请根据之前的记录回顾我的偏好，再解释一下认知行为疗法。"
    plan = guarantee_requested_reads(plan_for_route("chat", message, "test"), message)
    assert plan.intent == "knowledge"
    assert plan.required_tools == ["memory_search", "search_knowledge"]
    assert plan.max_tool_calls == 2


def test_one_sentence_definition_after_recall_keeps_both_resources():
    message = "先根据以前的记录告诉我喜欢听什么，再用一句话解释CBT。"
    plan = guarantee_requested_reads(plan_for_route("memory", message, "test"), message)
    assert plan.required_tools == ["memory_search", "search_knowledge"]


def test_goal_title_quotes_do_not_authorize_quoted_commands():
    from psych_agent_runtime.goal_creation import explicit_creation

    cases = json.loads(
        (Path(__file__).parents[2] / "contracts/decision-guard-cases.json").read_text()
    )["creation"]
    for row in cases:
        assert explicit_creation(row["text"]) == row["allowed"], row["text"]


def test_public_definition_does_not_open_unrequested_personal_memory():
    message = "什么是认知行为疗法？"
    plan = guarantee_requested_reads(plan_for_route("chat", message, "test"), message)
    assert plan.required_tools == ["search_knowledge"]
    assert "memory_search" not in plan.allowed_tools


def test_saved_goal_completion_criteria_survive_typed_and_prompt_boundaries():
    from psych_agent_runtime.schemas import GoalContext

    context = GoalContext.model_validate(
        {
            "goals": [
                {
                    "id": 1,
                    "version": 0,
                    "title": "规律休息",
                    "completionCriteria": "真实散步满五分钟。",
                    "status": "active",
                    "actions": [],
                    "updatedAt": "2026-10-13",
                }
            ],
            "actionLibrary": [],
        }
    )
    text, _ = bounded_tool_context({"get_goal_context": context.model_dump()}, TokenEstimator())
    assert "真实散步满五分钟。" in text


def test_three_read_requests_defer_excess_instead_of_exceeding_two_call_budget():
    question = "查看我的报告，看看我的目标有哪些步骤，再回顾我之前提到的偏好。"
    plan = guarantee_requested_reads(plan_for_route("chat", question, "test"), question)
    assert plan.required_tools == ["get_dashboard", "get_goal_context"]
    assert plan.max_tool_calls == 2
    assert plan.routing["deferredReads"] == ["memory_search"]


def test_repeated_questions_do_not_crowd_out_sources_but_events_keep_dates(tmp_path):
    store = MemoryStore(str(tmp_path / "memory.db"))
    question = "我之前提到喜欢听什么、在哪里休息？"
    facts = ["我喜欢听笛子音乐。", "我喜欢在阳台休息。", "我今天散步了。", "我今天散步了。"]
    for i, content in enumerate(facts + [question] * 30):
        store.ingest(
            {
                "username": "a",
                "sessionId": 1,
                "requestId": str(i),
                "userMessage": content,
                "assistantReply": "",
                "createdAt": f"2026-10-12T10:{i:02d}:00",
            }
        )
    selected = store.candidates("a", question)
    assert any(c["content"] == facts[0] for c in selected)
    assert any(c["content"] == facts[1] for c in selected)
    events = store.candidates("a", "我今天散步了", 8)
    assert sum(c["content"] == "我今天散步了。" for c in events) == 2
    assert store.candidates("b", question) == []


@pytest.mark.parametrize(
    "text,expected",
    [
        ("我之前提到喜欢听什么、在哪里休息？", True),
        ("我之前提到喜欢什么，我喜欢听笛子音乐。", False),
        ("你记得我昨天散步了五分钟吗？", False),
        ("我喜欢在安静的地方休息。", False),
    ],
)
def test_query_only_boundary_does_not_delete_asserted_facts(text, expected):
    assert recall_only_question(text) is expected


@pytest.mark.parametrize(
    "query",
    [
        "我姐姐喜欢听什么、在哪里休息？",
        "我不喜欢什么音乐、在哪里休息？",
    ],
)
def test_facets_do_not_rewrite_subject_location_domain_or_negation(query):
    assert query_facets(query) == []


def test_residence_question_does_not_become_a_rest_place_or_preferred_residence():
    facets = query_facets("我喜欢听什么音乐、住在哪里？")
    assert len(facets) == 2
    assert "住在哪里" in facets[1].query
    assert "休息" not in facets[1].query and "喜欢" not in facets[1].query


@pytest.mark.parametrize(
    "query,content,blocked",
    [
        ("我给谁寄了贺卡？", "舅舅给我寄了贺卡。", True),
        ("我给谁寄了贺卡？", "我给舅舅寄了贺卡。", False),
        ("我给谁寄了贺卡？", "我没有给任何人寄贺卡，舅舅给我寄了。", False),
        ("我给谁寄了贺卡？", "舅舅给我寄了，我给同事寄了。", False),
        ("我给谁寄了贺卡？", "舅舅给我寄了，我也寄给了同事。", False),
        ("谁给我寄了贺卡？", "舅舅给我寄了贺卡。", False),
        ("Who did I call yesterday?", "My aunt called me yesterday.", True),
        ("Who did I call yesterday?", "I called my aunt yesterday.", False),
        ("Who did I call yesterday?", "My aunt called me, and I called my tutor.", False),
        ("Who did I call yesterday?", "My aunt called me, and I also called my tutor.", False),
    ],
)
def test_direction_guard_only_blocks_clear_reverse_sources(query, content, blocked):
    assert clearly_reverse_direction(query, {"content": content}) is blocked


@pytest.mark.parametrize("status", ["ready", "limited", "fallback"])
def test_overview_is_available_when_legacy_reports_are_empty(status):
    result = dashboard_with_availability(
        {
            "reportSummaryResponse": [],
            "conversationOverview": {
                "status": status,
                "summary": "有一段对话记录。",
                "coverage": {"expressionCount": 2, "activeDays": 1},
            },
        }
    )
    availability = result["reportAvailability"]
    assert availability["answerKind"] == "overview"
    assert availability["hasConversationOverview"] and not availability["hasFormalReports"]
    assert availability["overviewLimited"]


def test_bounded_tool_json_preserves_memory_and_goal_steps_without_mutating_results():
    estimator = TokenEstimator()
    results = {
        "memory_search": {
            "status": "controlled",
            "evidence": [
                {"role": "user", "content": "我喜欢听笛子音乐。"},
                {"role": "user", "content": "我喜欢在阳台休息。"},
            ],
        },
        "get_goal_context": {
            "goals": [
                {
                    "title": "休息",
                    "status": "active",
                    "actions": [
                        {"title": t, "status": "planned"} for t in ["散步", "喝水", "记感受"]
                    ],
                }
            ],
            "actionLibrary": [{"description": "材料" * 1000}],
        },
    }
    text, truncated = bounded_tool_context(results, estimator)
    data = json.loads(text)
    assert truncated and estimator.count(text) < 800
    assert len(data["memory_search"]["evidence"]) == 2
    assert len(data["get_goal_context"]["goals"][0]["actions"]) == 3
    assert results["get_goal_context"]["actionLibrary"]


@pytest.mark.asyncio
async def test_multi_field_scoring_is_one_bounded_batch_and_partial_is_never_sufficient(tmp_path):
    service = MemoryService(
        Settings(
            memory_provider="qwen",
            memory_contract_version="psych-memory-v4",
            memory_store_path=str(tmp_path / "memory.db"),
        )
    )
    records = [
        {"id": "count", "role": "user", "content": "我本周游泳了三次。"},
        {"id": "duration", "role": "user", "content": "每次都是十五分钟。"},
    ]
    service.store.candidates = lambda *a: records

    async def score(states, task):
        assert len(states) <= 16
        return [
            {
                "accepted": True,
                "choice": "yes"
                if (
                    "几次" in s["query"]
                    and "三次" in s["candidate"]["content"]
                    or "多久" in s["query"]
                    and "十五分钟" in s["candidate"]["content"]
                )
                else "no",
                "probability": 0.99,
            }
            for s in states
        ]

    service.controller.evaluate_many = AsyncMock(side_effect=score)
    service.controller.evaluate = AsyncMock(
        return_value={"decisions": {"evidence_sufficient": {"choice": "yes", "accepted": True}}}
    )
    try:
        result = await service.retrieve("alice", "我本周游泳了几次，每次多久？")
        assert len(result["evidence"]) == 2 and result["status"] == "sufficient"
        assert service.controller.evaluate_many.await_count == 1
        records.pop()
        result = await service.retrieve("alice", "我本周游泳了几次，每次多久？")
        assert result["status"] == "controlled"
        assert result["retrievalCoverage"]["missing"] == ["part_2"]
    finally:
        await service.close()


@pytest.mark.asyncio
async def test_connected_empty_knowledge_is_not_reported_as_ready():
    from types import SimpleNamespace

    from psych_agent_runtime.rag import RagEngine

    rag = RagEngine.__new__(RagEngine)
    rag.settings = Settings()
    rag._ensure_collection = AsyncMock(return_value=SimpleNamespace(count=lambda: 0))
    info = await rag.knowledge_info()
    assert info["status"] == "empty" and info["chunks"] == 0
    rag._ensure_collection.side_effect = ConnectionError("unavailable fixture")
    info = await rag.knowledge_info()
    assert info["status"] == "unavailable" and info["chunks"] is None
