import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from psych_agent_runtime.memory_ledger import MemoryLedger
from psych_agent_runtime.memory_projection import MemoryProjection
from psych_agent_runtime.memory_shadow import MemoryLifecycleShadow
from psych_agent_runtime.memory_source_review import review_assertions
from tests.test_memory_projection import job


def model_decisions(*tasks):
    return {
        "model": "test-4b",
        "decisions": {k: {"accepted": True, "choice": "yes"} for k in tasks},
    }


@pytest.mark.asyncio
async def test_existing_jev_approval_can_be_vetoed_without_a_fact_write(tmp_path):
    source = job("我是不是已经养成每天早起的习惯了？")
    generation = SimpleNamespace(
        chat_json=AsyncMock(return_value={"decisions": [{"index": 0, "asserted": False}]})
    )
    ledger = MemoryLedger(tmp_path / "ledger")
    shadow = MemoryLifecycleShadow(
        ledger,
        "http://unused",
        "test",
        source_reviewer=generation,
        review_model="test-review",
        review_provider="test",
    )
    shadow.controller.evaluate = AsyncMock(side_effect=lambda state, tasks: model_decisions(*tasks))
    try:
        preview = await shadow.inspect(source)
        assert preview["proposals"][0]["action"] == "skip"
        assert preview["proposals"][0]["decisions"]["source_asserted"]["choice"] == "no"
        projected = MemoryProjection(ledger, {"alice"}, require_source_review=True).project(
            source, preview
        )
        assert not any(p.get("applied") for p in projected)
        assert ledger.candidates("alice") == []
        generation.chat_json.assert_awaited_once()
    finally:
        await shadow.close()


@pytest.mark.asyncio
async def test_mixed_candidates_share_one_source_review_and_keep_the_original_spans(tmp_path):
    source = job("我平时喜欢听爵士乐，我的职业是教师。")
    generation = SimpleNamespace(
        chat_json=AsyncMock(
            return_value={
                "decisions": [{"index": 0, "asserted": True}, {"index": 1, "asserted": False}]
            }
        )
    )
    ledger = MemoryLedger(tmp_path / "ledger")
    shadow = MemoryLifecycleShadow(
        ledger, "http://unused", "test", source_reviewer=generation, review_model="test-review"
    )
    shadow.controller.evaluate = AsyncMock(side_effect=lambda state, tasks: model_decisions(*tasks))
    try:
        preview = await shadow.inspect(source)
        assert [p["action"] for p in preview["proposals"]] == ["add", "skip"]
        projected = MemoryProjection(ledger, {"alice"}, require_source_review=True).project(
            source, preview
        )
        assert sum(p.get("applied") is True for p in projected) == 1
        assert ledger.candidates("alice")[0]["content"] == "我平时喜欢听爵士乐"
        generation.chat_json.assert_awaited_once()
        payload = json.loads(generation.chat_json.call_args.args[0][1]["content"])
        assert payload["source"] == source["userMessage"]
        assert payload["candidates"][1]["content"] == "我的职业是教师。"
    finally:
        await shadow.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "reply",
    [
        {},
        {"decisions": []},
        {"decisions": [{"index": 0, "asserted": "yes"}]},
        {"decisions": [{"index": 4, "asserted": True}]},
    ],
)
async def test_malformed_or_misaligned_review_never_counts_as_authorization(reply):
    generation = SimpleNamespace(chat_json=AsyncMock(return_value=reply))
    with pytest.raises(ValueError):
        await review_assertions(generation, "我喜欢音乐。", [(0, len("我喜欢音乐。"), "stable")])


@pytest.mark.asyncio
async def test_failed_review_preserves_an_audit_but_does_not_write(tmp_path):
    source = job("我的职业是教师。")
    generation = SimpleNamespace(chat_json=AsyncMock(side_effect=TimeoutError))
    ledger = MemoryLedger(tmp_path / "ledger")
    shadow = MemoryLifecycleShadow(ledger, "http://unused", "test", source_reviewer=generation)
    shadow.controller.evaluate = AsyncMock(side_effect=lambda state, tasks: model_decisions(*tasks))
    try:
        preview = await shadow.inspect(source)
        assert preview["proposals"][0]["action"] == "review"
        assert preview["proposals"][0]["previewError"] == "TimeoutError"
        assert not any(
            p.get("applied")
            for p in MemoryProjection(ledger, {"alice"}, require_source_review=True).project(
                source, preview
            )
        )
    finally:
        await shadow.close()


@pytest.mark.asyncio
async def test_source_reviewer_cannot_upgrade_a_jev_rejection(tmp_path):
    source = job("我今天有点开心。")
    generation = SimpleNamespace(chat_json=AsyncMock())
    shadow = MemoryLifecycleShadow(
        MemoryLedger(tmp_path / "ledger"), "http://unused", "test", source_reviewer=generation
    )
    shadow.controller.evaluate = AsyncMock(
        return_value={
            "model": "test-4b",
            "decisions": {
                "write_worthy": {"accepted": True, "choice": "no"},
                "grounded": {"accepted": True, "choice": "yes"},
            },
        }
    )
    try:
        assert (await shadow.inspect(source))["proposals"][0]["action"] == "skip"
        generation.chat_json.assert_not_awaited()
    finally:
        await shadow.close()


@pytest.mark.asyncio
async def test_vetoed_question_cannot_return_through_raw_or_recent_bridge_even_after_audit_rotation(
    tmp_path,
):
    from psych_agent_runtime.memory import MemoryStore
    from psych_agent_runtime.memory_read import MemoryReadView

    source = job("我是不是已经养成每天早起的习惯了？")
    store = MemoryStore(tmp_path / "raw")
    store.ingest(source)
    ledger = MemoryLedger(tmp_path / "ledger", shadow_limit=1)
    before = MemoryReadView(ledger, store, "alice")
    generation = SimpleNamespace(
        chat_json=AsyncMock(return_value={"decisions": [{"index": 0, "asserted": False}]})
    )
    shadow = MemoryLifecycleShadow(
        ledger, "http://unused", "test", source_reviewer=generation, review_model="test-review"
    )
    shadow.controller.evaluate = AsyncMock(side_effect=lambda state, tasks: model_decisions(*tasks))
    try:
        await shadow.inspect(source)
        assert not before.unchanged()  # Veto alone invalidates an older read snapshot.
        generation.chat_json.return_value = {"decisions": [{"index": 0, "asserted": True}]}
        await shadow.inspect(job("我的职业是教师。", "later"))
        # The diagnostic audit is bounded, but its veto must survive eviction.
        assert len(ledger.nonasserted_sources("alice")) == 1
        raw = store.read_pool("alice")
        alias = {**raw[0], "id": "sql", "source": "conversation_message:1"}
        assert MemoryReadView(ledger, store, "alice").assemble([*raw, alias]) == []
        assert MemoryReadView(ledger, store, "alice", include_questions=True).assemble(raw)
        assert MemoryReadView(ledger, store, "bob").assemble(store.read_pool("bob")) == []
        assert "content" not in ledger.nonasserted_sources("alice")[0]
        with store.connect() as db:
            db.execute(
                "UPDATE observations SET content='changed' WHERE owner='alice' AND role='user'"
            )
        with pytest.raises(ValueError, match="veto source"):
            MemoryReadView(ledger, store, "alice")
    finally:
        await shadow.close()
