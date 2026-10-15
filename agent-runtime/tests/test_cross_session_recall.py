import json
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import pytest

from psych_agent_runtime.capabilities import normalize_result
from psych_agent_runtime.config import Settings
from psych_agent_runtime.memory import MemoryService, MemoryStore
from psych_agent_runtime.memory_query import recall_only_question
from psych_agent_runtime.memory_source_guard import reviewable_personal_source
from psych_agent_runtime.personal_recall import personal_recall_request
from psych_agent_runtime.response_context import bounded_tool_context
from psych_agent_runtime.route_policy import guarantee_requested_reads, plan_for_route
from psych_agent_runtime.schemas import AgentContext
from psych_agent_runtime.token_budget import TokenEstimator
from psych_agent_runtime.tool_activity import result_summary
from psych_agent_runtime.tool_controller import ToolController


@pytest.mark.parametrize("text,today", [
    ("今天我的情绪咋样呀？", True),
    ("我今天是开心还是低落？", True),
    ("我平时都怎么放松的？", False),
    ("还记得我爱听啥吗？", False),
    ("我家的兔子叫什么来着？", False),
    ("上次我说通过了什么试课？", False),
    ("之前我提到试课的结果是什么？", False),
    ("今天我是什么心情呀？", True),
])
def test_natural_fact_queries_require_lookup_in_established_session(text, today):
    context = AgentContext(username="alice", sessionId=8,
                           messages=[{"role": "user", "content": "聊聊别的"}] * 8)
    plan = guarantee_requested_reads(plan_for_route("chat", text, "test"), text, context)
    assert plan.intent == "memory" and plan.required_tools == ["memory_search"]
    assert (plan.routing["personalRecall"]["timeWindow"] == "today") is today
    assert recall_only_question(text)


@pytest.mark.parametrize("text", [
    "我平时喜欢听轻音乐。", "我今天很高兴。", "我家的兔子叫芝麻团。",
    "我今天还是很高兴。",
    "我平时应该怎么放松？", "我喜欢听音乐，这样能缓解焦虑吗？",
    "朋友的兔子叫什么？", "不要查记录，我平时怎么放松？",
    "明天我的情绪咋样呀？", "解释这句话：“我家的兔子叫什么？”",
])
def test_new_fact_read_guarantee_does_not_capture_statements_advice_or_optouts(text):
    assert personal_recall_request(text) is None


@pytest.mark.parametrize("text", [
    "今天我很高兴。我家的兔子叫什么来着？",
    "我喜欢听轻音乐，你还记得我平时在哪放松吗？",
    "我家的兔子叫什么？我养的兔子叫芝麻团。",
    "我还是喜欢听轻音乐。我家的兔子叫什么？",
])
def test_mixed_facts_and_questions_remain_evidence_candidates(text):
    assert not recall_only_question(text)


@pytest.mark.parametrize("text", [
    "我朋友养的兔子叫什么？", "我的生日是几月几日？",
    "不用查之前的记录，我只是想随便聊聊。",
])
def test_empty_fact_slots_and_chat_controls_do_not_occupy_answer_candidates(text):
    assert recall_only_question(text)


def ingest(store, owner, session, text, i):
    store.ingest({"username": owner, "sessionId": session, "requestId": f"r-{i}",
                  "userMessage": text, "assistantReply": "not user evidence",
                  "createdAt": (datetime(2026, 10, 15, tzinfo=UTC) + timedelta(minutes=i)).isoformat()})


def test_questions_are_removed_before_lexical_topk_and_recent_window(tmp_path):
    store = MemoryStore(str(tmp_path / "index.db"))
    fact = "我喜欢听轻音乐放松。"
    ingest(store, "alice", 1, fact, 0)
    ingest(store, "bob", 2, "我喜欢摇滚。", 0)
    for i in range(1, 33):
        ingest(store, "alice", 3, f"我之前说过喜欢什么放松活动？请简短回答，问题{i}", i)
    query = "你记得我喜欢什么放松活动吗？"
    for candidates in (store.candidates("alice", query, 1),
                       store.recent("alice", 4, query=query)):
        assert [r["content"] for r in candidates] == [fact]
    # Asking about questions deliberately keeps question records searchable.
    questions = store.candidates("alice", "我问过什么放松活动的问题？", 2)
    assert any("问题" in r["content"] for r in questions)


@pytest.mark.asyncio
async def test_reranker_never_spends_semantic_slots_on_question_only_records(tmp_path):
    service = MemoryService(Settings(memory_provider="qwen", memory_store_path=str(tmp_path / "m.db")))
    fact = {"id": "fact", "role": "user", "sessionId": 1, "content": "我喜欢笛子音乐。"}
    question = {"id": "question", "role": "user", "sessionId": 2,
                "content": "你记得我喜欢什么放松活动吗？"}
    service.store.candidates = lambda *args, **kw: [question]
    service.store.recent = lambda *args, **kw: [question, fact]
    service.reranker = AsyncMock()
    service.reranker.order.return_value = ([fact], "0.6b")
    service.controller.evaluate_many = AsyncMock(return_value=[{
        "choice": "yes", "accepted": True, "probability": 0.99,
    }])
    service.controller.evaluate = AsyncMock(return_value={"decisions": {
        "evidence_sufficient": {"choice": "yes", "accepted": True},
    }})
    try:
        result = await service.retrieve("alice", "你记得我喜欢什么放松活动吗？")
        assert service.reranker.order.call_args.args[1] == [fact]
        assert result["evidence"] == [fact]
    finally:
        await service.close()


@pytest.mark.asyncio
async def test_rejected_candidates_can_be_reviewed_without_relabeling_or_extra_model_calls(tmp_path):
    service = MemoryService(Settings(memory_provider="qwen", memory_store_path=str(tmp_path / "m.db")))
    ingest(service.store, "alice", 1, "我喜欢听轻音乐。", 0)
    ingest(service.store, "bob", 2, "我喜欢重金属。", 0)
    no = {"choice": "no", "accepted": True, "probability": 0.99}
    service.controller.evaluate_many = AsyncMock(return_value=[no])
    service.controller.evaluate = AsyncMock(return_value={"decisions": {"evidence_sufficient": no}})
    try:
        result = await service.retrieve("alice", "我一般喜欢听什么类型的音乐？")
        result = normalize_result("memory_search", result)
        assert result["status"] == "controlled" and result["evidence"] == []
        assert [r["content"] for r in result["candidateEvidence"]] == ["我喜欢听轻音乐。"]
        service.controller.evaluate_many.assert_awaited_once()
        service.controller.evaluate.assert_awaited_once()
        assert "待核对" in result_summary("memory_search", result)
        text, _ = bounded_tool_context({"memory_search": result}, TokenEstimator())
        assert '"evidence": []' in text and '"candidateEvidence"' in text
        # Candidate review does not enter unsolicited background context.
        background = await service.retrieve_context("alice", "我有点累，想放松。", 9)
        assert background.get("candidateEvidence") == []
    finally:
        await service.close()


@pytest.mark.parametrize("query,record", [
    ("我朋友的兔子叫什么？", {"role": "user", "content": "我的兔子叫团团。"}),
    ("我的兔子叫什么？", {"role": "assistant", "content": "你的兔子叫团团。"}),
    ("我的兔子叫什么？", {"role": "user", "content": "朋友的兔子叫团团。"}),
    ("我的兔子叫什么？", {"role": "user", "content": "我的兔子叫团团。", "truncated": True}),
])
def test_candidate_review_keeps_source_role_subject_and_completeness_boundaries(query, record):
    assert not reviewable_personal_source(query, record)


@pytest.mark.asyncio
async def test_candidate_review_finishes_without_redundant_recovery_call():
    controller = ToolController(Settings(executor_provider="rules"))
    loop = controller.start_loop(plan_for_route("memory", "我喜欢什么音乐？", "test"), "query")
    loop["next"] = {"tool": "memory_search", "arguments": {"username": "alice", "query": "query"}}
    loop["pending_result"] = {"status": "controlled", "evidence": [], "candidateEvidence": [{"content": "raw"}]}
    loop = controller.observe(loop, lambda *args: None)
    assert loop["observations"][0]["status"] == "needs_review"
    refiner = AsyncMock()
    try:
        result = await controller.decide(loop, lambda *args: None, refiner)
        assert result["next"]["tool"] == "finish"
        refiner.assert_not_awaited()
    finally:
        await controller.close()


def test_candidate_quote_is_omitted_whole_when_response_budget_is_too_small():
    source = "我喜欢听轻音乐。" * 35 + "更正：以上只是文章里的引文，不是我的偏好。"
    result = {"status": "controlled", "evidence": [], "candidateEvidence": [
        {"role": "user", "content": source, "truncated": False},
    ]}
    text, truncated = bounded_tool_context({"memory_search": result}, TokenEstimator(), budget=128)
    assert truncated and json.loads(text)["memory_search"]["candidateEvidence"] == []
    assert result["candidateEvidence"][0]["content"] == source


def test_source_followup_rechecks_nearest_user_fact_without_trusting_assistant_claims():
    context = AgentContext(username="alice", sessionId=2, messages=[
        {"role": "user", "content": "我家的兔子叫什么来着？"},
        {"role": "assistant", "content": "叫某个未经证实的名字。"},
    ])
    plan = guarantee_requested_reads(plan_for_route("chat", "你是怎么知道的？", "test"),
                                     "你是怎么知道的？", context)
    assert plan.required_tools == ["memory_search"]
    assert plan.routing["personalRecall"]["sourceFollowUp"]
    assert "兔子" in plan.routing["personalRecall"]["query"]
    assert "未经证实" not in plan.routing["personalRecall"]["query"]
    context.messages.append({"role": "user", "content": "什么是深呼吸？"})
    assert personal_recall_request("你是怎么知道的？", context) is None
    assert personal_recall_request("你是怎么知道的？") is None
    assert personal_recall_request("不要查记录，你是怎么知道的？", context) is None
