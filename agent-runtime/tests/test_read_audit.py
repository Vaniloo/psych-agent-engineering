import json
from types import SimpleNamespace

import pytest

from benchmarks.runtime_fixture import make_runtime

from psych_agent_runtime.config import Settings
from psych_agent_runtime.memory import MemoryStore
from psych_agent_runtime.memory_query import recall_only_question
from psych_agent_runtime.read_audit import read_audit
from psych_agent_runtime.response_context import bounded_tool_context
from psych_agent_runtime.route_policy import guarantee_requested_reads, plan_for_route
from psych_agent_runtime.schemas import AgentContext
from psych_agent_runtime.token_budget import TokenEstimator
from benchmarks.collect_read_failures import collect, export
from psych_agent_runtime.telemetry import start_run, end_run


@pytest.mark.parametrize("message", ["你对我有什么了解", "你对我有什么了解吗", "我其他对话里面说了什么"])
def test_broad_recaps_receive_lookup_permissions_in_established_conversations(message):
    context = AgentContext(username="alice", sessionId=8, messages=[{"role": "user", "content": "无关话题"}] * 8)
    plan = guarantee_requested_reads(plan_for_route("chat", message, "test"), message, context)
    assert plan.required_tools == ["memory_search"]
    runtime = make_runtime()
    args = runtime._loop_arguments("memory_search", {"username": "alice", "session_id": 8, "message": message}, plan)
    assert args["purpose"] == "lookup" and args["recent_messages"] and "session_id" not in args


@pytest.mark.parametrize("message", [
    "不要查记录，你对我有什么了解", "解释这句话：“你对我有什么了解”", "我朋友的其他对话里面说了什么",
])
def test_quoted_declined_and_other_person_recaps_do_not_force_a_lookup(message):
    plan = guarantee_requested_reads(plan_for_route("chat", message, "test"), message)
    assert "memory_search" not in plan.required_tools


@pytest.mark.parametrize("text", ["我最近怎么了", "你对我有什么了解", "我其他对话里面说了什么"])
def test_recap_questions_cannot_occupy_answer_source_slots(text):
    assert recall_only_question(text)
    assert not recall_only_question("我最近很焦虑。你对我有什么了解？")


def test_prompt_coverage_records_candidate_removal_without_storing_its_text():
    result = {"status": "controlled", "evidence": [], "candidateEvidence": [
        {"role": "user", "content": "这是一条完整的长原话" * 300, "sessionId": 1,
         "observedAt": "2026-10-19T10:00:00+08:00"}]}
    audit = {}
    bounded_tool_context({"memory_search": result}, TokenEstimator(), budget=180, audit=audit)
    assert audit["memoryReviewBefore"] == 1 and audit["memoryReviewInPrompt"] == 0
    flagged = read_audit({"message": "你对我有什么了解", "tool_results": {"memory_search": result},
                         "context_stats": {"toolEvidence": audit}})
    assert "memory_response_context_reduced" in flagged["signals"]
    assert "长原话" not in json.dumps(flagged, ensure_ascii=False)


def test_review_signals_distinguish_missing_calls_from_empty_or_partial_results_without_text():
    message = "我最近经常吃什么"
    state = {"message": message, "reply": "PRIVATE_REPLY", "plan": {"intent": "memory"}}
    assert read_audit(state)["signals"] == ["memory_read_missing"]
    state["tool_results"] = {"memory_search": {"status": "controlled", "evidence": [],
        "candidateEvidence": [{"content": "PRIVATE_SOURCE"}], "audit": {"candidateSourceIds": ["source-1"],
        "content": "PRIVATE_SOURCE"}, "retrievalCoverage": {"missing": ["part_2"]}}}
    audit = read_audit(state)
    assert "memory_read_missing" not in audit["signals"]
    assert {"memory_no_accepted_evidence", "memory_evidence_needs_review", "memory_partial_coverage"} <= set(audit["signals"])
    assert audit["classification"] == "suspected_requires_review"
    assert all(text not in json.dumps(audit, ensure_ascii=False) for text in [message, "PRIVATE_REPLY", "PRIVATE_SOURCE"])
    assert not read_audit({"message": "想随便聊聊", "tool_results": state["tool_results"]})["signals"]


def test_review_queue_is_idempotent_bounded_scoped_and_removed_with_memory(tmp_path):
    store = MemoryStore(str(tmp_path / "index.sqlite3"))
    for i in range(202):
        store.record_read_audit("alice", str(i), {"signals": ["needs_review"]})
    store.record_read_audit("bob", "one", {"signals": ["needs_review"]})
    store.record_read_audit("alice", "201", {"signals": ["updated"]})
    store.record_read_audit("alice", "ordinary", {"signals": []})
    with store.connect() as db:
        assert db.execute("SELECT count(*) FROM read_audits WHERE owner='alice'").fetchone()[0] == 200
        assert db.execute("SELECT metadata FROM read_audits WHERE owner='alice' AND request_id='201'").fetchone()[0] == '{"signals": ["updated"]}'
    store.forget("alice")
    with store.connect() as db:
        assert db.execute("SELECT count(*) FROM read_audits").fetchone()[0] == 1


def test_export_is_owner_scoped_private_and_does_not_make_failure_or_training_labels(tmp_path):
    database = tmp_path / "index.sqlite3"
    store = MemoryStore(str(database))
    for owner in ["alice", "bob"]:
        store.ingest({"username": owner, "sessionId": 1, "requestId": "same-request",
                      "userMessage": owner + " PRIVATE", "assistantReply": "reply",
                      "createdAt": "2026-10-19T10:00:00+08:00"})
        store.record_read_audit(owner, "same-request", {"signals": ["memory_no_accepted_evidence"]})
    cases = collect(database, "alice")
    assert len(cases) == 1 and "dialogue" not in cases[0]
    assert cases[0]["verdict"] == "unreviewed" and not cases[0]["trainingEligible"]
    details = collect(database, "alice", include_dialogue=True)
    assert "alice PRIVATE" in json.dumps(details) and "bob PRIVATE" not in json.dumps(details)
    summary = export(database, "alice", tmp_path / "private-export")
    assert summary["cases"] == 1
    assert (tmp_path / "private-export/cases.jsonl").stat().st_mode & 0o777 == 0o600


@pytest.mark.asyncio
async def test_finalization_persists_review_metadata_without_a_second_transcript(tmp_path):
    runtime = make_runtime()
    runtime.settings = Settings()
    runtime.memory = SimpleNamespace(store=MemoryStore(str(tmp_path / "index.sqlite3")))
    runtime._emit = lambda *_: None
    _, token = start_run(runtime.settings, "audit-test")
    try:
        result = await runtime._finalize_metrics({"username": "alice", "request_id": "audit-test",
            "message": "你对我有什么了解", "reply": "PRIVATE_REPLY", "persistence_status": "saved"})
        assert result["metrics"]["pipeline"]["readAudit"]["signals"] == ["memory_read_missing"]
        cases = collect(runtime.memory.store.path, "alice")
        assert len(cases) == 1
        assert "PRIVATE_REPLY" not in json.dumps(cases) and "你对我" not in json.dumps(cases, ensure_ascii=False)
    finally:
        end_run(token)


