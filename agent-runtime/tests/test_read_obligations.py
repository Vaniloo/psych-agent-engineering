"""Read requirements survive a wrong route or a model's early TEXT choice."""

import pytest

from benchmarks.public_tool_replay import graph_case
from benchmarks.runtime_fixture import make_runtime

from psych_agent_runtime.memory_query import recall_only_question
from psych_agent_runtime.personal_recall import personal_recall_request
from psych_agent_runtime.route_policy import guarantee_requested_reads, plan_for_route
from psych_agent_runtime.support_knowledge import with_support_knowledge


class AlwaysText:
    async def score(self, row, mode):
        return {"scores": [1.0 if c["kind"] == "assistant_text" else -1.0
                           for c in row["candidateTools"]], "durationMs": 0, "maxTokens": 1}


@pytest.mark.asyncio
@pytest.mark.parametrize("message", [
    "请查看我的心理报告，并回顾我以前喜欢听什么。",
    "我以前喜欢听什么？请查看我的心理报告。",
    "查看我的心理报告，我最近经常吃什么？",
])
async def test_compound_reads_all_run_even_when_executor_always_prefers_text(message):
    result = await graph_case(AlwaysText(), "candidate", "compound", message, "chat",
                              ["get_dashboard", "memory_search"])
    assert result["passed"], result
    calls = {call["tool"]: call["arguments"] for call in result["calls"]}
    args = calls["memory_search"]
    assert args["purpose"] == "lookup" and args["recent_messages"]
    assert "报告" not in args["query"]
    assert args.get("session_id") is None  # Validated optional field defaults to None.


@pytest.mark.parametrize("message", [
    "给我一点压力应对方法", "教我一些焦虑缓解技巧", "压力怎么应对？",
])
@pytest.mark.asyncio
async def test_requested_coping_knowledge_cannot_be_skipped_by_wrong_chat_route_or_text(message):
    result = await graph_case(AlwaysText(), "candidate", "coping", message, "chat", ["search_knowledge"])
    assert result["passed"], result


@pytest.mark.parametrize("message", [
    "不要给我压力应对方法，只想聊聊", "例如给我一点压力应对方法",
    "解释这句话：“给我一点压力应对方法”", "教我一点Python编程技巧",
])
def test_method_read_guarantee_does_not_override_optout_quotes_or_other_topics(message):
    plan = guarantee_requested_reads(plan_for_route("chat", message, "test"), message)
    plan = with_support_knowledge(plan, message, "ordinary")
    assert "search_knowledge" not in plan.required_tools


@pytest.mark.parametrize("message", [
    "不要查记录，请查看我的心理报告，并回顾我以前喜欢听什么。",
    "请查看我的心理报告，例如我以前喜欢听什么。",
    "请查看我的心理报告，再解释这句话：“我以前喜欢听什么”。",
    "我以前喜欢听爵士乐。请查看我的心理报告。",
])
def test_compound_read_does_not_open_personal_memory_for_excluded_or_asserted_clauses(message):
    assert personal_recall_request(message) is None
    plan = guarantee_requested_reads(plan_for_route("chat", message, "test"), message)
    assert "memory_search" not in plan.required_tools


def test_mixed_statement_remains_evidence_and_distinct_time_scopes_are_not_collapsed_to_today():
    assert not recall_only_question("我今天去了书店。我最近看的电影是什么？")
    message = "我今天心情怎么样？我以前喜欢听什么？"
    plan = guarantee_requested_reads(plan_for_route("chat", message, "test"), message)
    runtime = make_runtime()
    args = runtime._loop_arguments("memory_search", {
        "message": message, "username": "alice", "session_id": 9,
    }, plan)
    assert args["time_window"] is None
    assert "心情" in args["query"] and "喜欢听什么" in args["query"]


@pytest.mark.parametrize("message", [
    "我之前提到喜欢听什么、在哪里休息？", "我喜欢听什么，在哪里休息？",
])
def test_shared_subject_questions_retain_all_requested_facets(message):
    spec = personal_recall_request(message)
    assert "喜欢听什么" in spec["query"] and "在哪里休息" in spec["query"]
