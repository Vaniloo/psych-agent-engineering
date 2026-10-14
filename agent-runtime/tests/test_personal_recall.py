from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from psych_agent_runtime.config import Settings
from psych_agent_runtime.graph import AgentGraphRuntime
from psych_agent_runtime.memory import MemoryService
from psych_agent_runtime.memory_query import recall_only_question
from psych_agent_runtime.memory_scope import merge_sources, time_scope
from psych_agent_runtime.personal_recall import personal_recall_request
from psych_agent_runtime.route_policy import guarantee_requested_reads, plan_for_route
from psych_agent_runtime.schemas import AgentContext
from psych_agent_runtime.token_budget import ContextBudgetManager


@pytest.mark.parametrize(
    "message",
    [
        "我今天心情怎么样",
        "今天我的情绪如何？",
        "你看看我今天状态怎么样",
        "我今天过得怎么样？",
        "我今天心情",
    ],
)
def test_personal_recap_is_a_required_lookup_even_in_established_conversation(message):
    ctx = AgentContext(
        username="alice", sessionId=2, messages=[{"role": "user", "content": "无关的聊天"}] * 8
    )
    plan = guarantee_requested_reads(plan_for_route("chat", message, "test"), message, ctx)
    assert plan.intent == "memory"
    assert plan.required_tools == ["memory_search"]
    assert plan.routing["personalRecall"]["timeWindow"] == "today"
    r = AgentGraphRuntime.__new__(AgentGraphRuntime)
    args = r._loop_arguments(
        "memory_search", {"username": "alice", "session_id": 2, "message": message}, plan
    )
    assert args["purpose"] == "lookup" and args["recent_messages"]
    assert args["time_window"] == "today"
    assert "session_id" not in args  # Lookup may use current and other owned sessions.


@pytest.mark.parametrize(
    "message",
    [
        "我今天心情很好",
        "今天我有点高兴",
        "明天我心情怎么样？",
        "朋友今天心情怎么样？",
        "解释这句话：“我今天心情怎么样”",
        "不要查记录，我今天心情怎么样",
        "只根据这次回答，我今天心情怎么样",
    ],
)
def test_statement_other_person_future_quote_and_optout_do_not_trigger_personal_recap(message):
    assert personal_recall_request(message) is None


def test_short_followup_and_recent_dialogue_inherit_own_query_not_assistant_claims():
    ctx = AgentContext(
        username="alice",
        sessionId=2,
        messages=[
            {"role": "user", "content": "我今天心情"},
            {"role": "assistant", "content": "不知道你今天的心情"},
            {"role": "user", "content": "怎么样"},
        ],
    )
    for message in ["怎么样", "你看看最近对话"]:
        plan = guarantee_requested_reads(plan_for_route("chat", message, "test"), message, ctx)
        assert plan.intent == "memory" and plan.routing["personalRecall"]["timeWindow"] == "today"
    assert personal_recall_request("怎么样", AgentContext(username="alice", sessionId=2)) is None
    assert personal_recall_request("你看看最近对话")["topic"] == "recent_conversations"
    ctx.messages.append(SimpleNamespace(role="user", content="换个话题"))
    assert personal_recall_request("怎么样", ctx) is None


@pytest.mark.parametrize("message", ["我今天心情怎么样", "我今天心情", "你看看最近对话"])
def test_recap_question_itself_is_not_emotional_evidence(message):
    assert recall_only_question(message)
    assert not recall_only_question("今天我有点高兴")


def row(i, content, observed="2026-10-14T10:00:00Z", role="user"):
    return {
        "id": str(i),
        "content": content,
        "role": role,
        "observedAt": observed,
        "sessionId": 1,
        "source": str(i),
        "timestampRole": "observation_time",
        "truncated": False,
    }


def test_today_scope_handles_timezone_midnight_past_event_and_latest_first():
    rows = [
        row(1, "今天我有点高兴", "2026-10-13T18:00:00Z"),
        row(2, "今天我有点焦虑", "2026-10-13T15:59:59Z"),
        row(3, "昨天我很高兴"),
        row(4, "今天刚才有点累", "2026-10-14T11:00:00"),
    ]
    selected, scope = time_scope(rows, "today", now=datetime(2026, 10, 14, 12, tzinfo=UTC))
    assert [r["id"] for r in selected] == ["4", "1"]
    assert scope["date"] == "2026-10-14" and scope["timezone"] == "Asia/Shanghai"
    assert scope["filteredOut"] == 2
    assert (
        len(
            merge_sources(
                [rows[0], {**rows[0], "id": "source-db", "observedAt": "2026-10-13T18:00:00+00:00"}]
            )
        )
        == 1
    )


@pytest.mark.asyncio
async def test_pending_index_uses_bounded_authoritative_user_sources_through_same_memory_flow(
    tmp_path,
):
    now = datetime.now(UTC).isoformat()
    backend = SimpleNamespace(
        recent_messages=AsyncMock(
            return_value=[
                row(1, "今天我有点高兴", now),
                row(2, "我今天心情怎么样", now),
                row(3, "助手编造的情绪", now, "assistant"),
            ]
        )
    )
    service = MemoryService(
        Settings(memory_provider="qwen", memory_store_path=str(tmp_path / "empty.db")), backend
    )
    seen = []

    async def evaluate_many(states, task):
        seen.extend(states)
        return [{"accepted": True, "choice": "yes", "probability": 0.99} for _ in states]

    service.controller.evaluate_many = AsyncMock(side_effect=evaluate_many)
    service.controller.evaluate = AsyncMock(
        return_value={"decisions": {"evidence_sufficient": {"accepted": True, "choice": "yes"}}}
    )
    try:
        result = await service.retrieve(
            "alice", "我今天表达过什么心情或感受？", recent_messages=True, time_window="today"
        )
        assert [r["content"] for r in result["evidence"]] == ["今天我有点高兴"]
        backend.recent_messages.assert_awaited_once_with("alice")
        assert result["sourceGuard"]["queryOnlyBlocked"] == 1
        assert result["retrievalCoverage"]["freshMessages"]["status"] == "completed"
        assert [s["candidate"]["role"] for s in seen] == ["user"]
        with service.store.connect() as db:
            assert db.execute("SELECT count(*) FROM observations").fetchone()[0] == 0
    finally:
        await service.close()


@pytest.mark.asyncio
async def test_recap_reply_uses_other_session_source_and_discards_stale_denial():
    seen = []

    async def stream(messages, **kwargs):
        seen.extend(messages)
        yield "你今天在另一段对话里说有点高兴。"

    r = AgentGraphRuntime.__new__(AgentGraphRuntime)
    r.settings = Settings()
    r.context_budget = ContextBudgetManager(8192, 1200)
    r.ollama = SimpleNamespace(stream_chat=stream)
    r._emit = lambda *_: None
    ctx = AgentContext(
        username="alice",
        sessionId=2,
        messages=[{"role": "assistant", "content": "STALE_DENIAL：我不知道你今天心情"}],
    )
    plan = guarantee_requested_reads(plan_for_route("chat", "", "test"), "我今天心情怎么样", ctx)
    result = await r._respond(
        {
            "context": ctx.model_dump(by_alias=True),
            "message": "我今天心情怎么样",
            "request_id": "fresh",
            "plan": plan.model_dump(),
            "tool_results": {
                "memory_search": {
                    "purpose": "lookup",
                    "status": "controlled",
                    "evidence": [row(1, "今天我有点高兴")],
                }
            },
        }
    )
    prompt = "\n".join(m["content"] for m in seen)
    assert "今天我有点高兴" in prompt and "STALE_DENIAL" not in prompt
    assert "不是请你猜测心情" in prompt
    assert "另一段对话" in result["reply"]


@pytest.mark.asyncio
async def test_expanded_fresh_pool_respects_sixteen_pair_worker_limit(tmp_path):
    now = datetime.now(UTC).isoformat()
    backend = SimpleNamespace(
        recent_messages=AsyncMock(
            return_value=[row(i, f"今天我有点高兴{i}", now) for i in range(24)]
        )
    )
    service = MemoryService(
        Settings(memory_provider="qwen", memory_store_path=str(tmp_path / "batch.db")), backend
    )
    sizes = []

    async def score(states, task):
        sizes.append(len(states))
        assert len(states) <= 16
        return [{"accepted": True, "choice": "yes", "probability": 0.99} for _ in states]

    service.controller.evaluate_many = AsyncMock(side_effect=score)
    service.controller.evaluate = AsyncMock(
        return_value={"decisions": {"evidence_sufficient": {"accepted": True, "choice": "yes"}}}
    )
    try:
        result = await service.retrieve(
            "alice", "我今天表达过哪些心情？", recent_messages=True, time_window="today"
        )
        assert sizes == [16, 8]
        assert len(result["evidence"]) == 6 and result["status"] == "controlled"
        assert all(r["observedAt"].endswith("+08:00") for r in result["evidence"])
    finally:
        await service.close()


def test_personal_state_does_not_inherit_unrelated_named_goal():
    ctx = AgentContext(
        username="alice",
        sessionId=2,
        messages=[{"role": "user", "content": "我想聊聊目标「准备面试」的进度"}],
    )
    plan = guarantee_requested_reads(plan_for_route("chat", "", "test"), "我今天状态怎么样", ctx)
    assert plan.intent == "memory" and plan.required_tools == ["memory_search"]
    assert "goalFocus" not in plan.routing
