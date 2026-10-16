from unittest.mock import AsyncMock

import httpx
import pytest

from psych_agent_runtime.config import Settings
from psych_agent_runtime.memory import MemoryService, MemoryStore
from psych_agent_runtime.memory_ledger import MemoryLedger, proposal
from psych_agent_runtime.memory_read import MemoryReadView
from tests.test_memory_projection import job, votes


def setup_history(tmp_path):
    store, ledger = MemoryStore(tmp_path / "raw.db"), MemoryLedger(tmp_path / "ledger.db")
    text = "我平时喜欢听轻音乐，我唯一一只兔子叫芝麻团。"
    source = job(text, "original")
    store.ingest(source)
    end = text.index("，")
    music = proposal(source, 0, end, scope="stable")
    pet = proposal(source, end + 1, len(text), scope="stable")
    ledger.apply(music, votes(), enabled=True)
    ledger.apply(pet, votes(), enabled=True)
    correction = job("更正：我平时喜欢听的是民谣，不是轻音乐。", "correction")
    correction["sessionId"] = 2
    store.ingest(correction)
    new = proposal(correction, 0, len(correction["userMessage"]), scope="stable")
    change = ledger.apply(new, votes(supersedes="yes", conflict="yes"),
                          target=ledger.get("alice", music["id"]), expected_version=1, enabled=True)
    return store, ledger, music, pet, change


def test_current_view_masks_superseded_span_preserves_pet_and_sql_alias(tmp_path):
    store, ledger, music, pet, _ = setup_history(tmp_path)
    raw = store.read_pool("alice")
    aliases = [{**r, "id": "sql:" + r["id"], "source": "conversation_message:" + r["id"]}
               for r in raw]
    view = MemoryReadView(ledger, store, "alice")
    records = view.assemble([*raw, *aliases])
    assert {r["id"] for r in records} == {pet["id"], ledger.get("alice", music["id"])["replacement_id"]}
    assert not any(r["content"] == music["content"] for r in records)
    assert any(r["content"] == pet["content"] for r in records)
    assert len(view.assemble(raw, exclude_session_id=2)) == 1
    assert MemoryReadView(ledger, store, "bob").assemble(store.read_pool("bob")) == []


def test_undo_does_not_reintroduce_correction_via_raw(tmp_path):
    store, ledger, music, pet, change = setup_history(tmp_path)
    before = MemoryReadView(ledger, store, "alice")
    ledger.undo("alice", change["changeId"], enabled=True)
    assert not before.unchanged()
    current = MemoryReadView(ledger, store, "alice").assemble(store.read_pool("alice"))
    assert {r["id"] for r in current} == {music["id"], pet["id"]}
    assert not any("民谣" in r["content"] for r in current)


def test_stable_and_temporary_clauses_remain_independent(tmp_path):
    store, ledger = MemoryStore(tmp_path / "raw"), MemoryLedger(tmp_path / "ledger")
    source = job("我的职业是教师，我今天有点开心。")
    store.ingest(source)
    item = proposal(source, 0, source["userMessage"].index("，"), scope="stable")
    ledger.apply(item, votes(), enabled=True)
    rows = MemoryReadView(ledger, store, "alice").assemble(store.read_pool("alice"))
    assert len(rows) == 2
    assert any(r["content"] == "我今天有点开心。" for r in rows)
    assert all(r["source"] == source["requestId"] for r in rows)


@pytest.mark.parametrize("damage", ["missing", "text", "session", "canonical"])
def test_invalid_source_fails_closed(tmp_path, damage):
    store, ledger, _, _, _ = setup_history(tmp_path)
    with store.connect() as db:
        if damage == "missing":
            db.execute("DELETE FROM observations WHERE source='original'")
        if damage == "text":
            db.execute("UPDATE observations SET content='changed' WHERE source='original'")
        if damage == "session":
            db.execute("UPDATE observations SET session=7 WHERE source='original'")
    if damage == "canonical":
        with ledger.connect() as db:
            db.execute("UPDATE memory_items SET content='invented' WHERE status='active'")
    with pytest.raises(ValueError):
        MemoryReadView(ledger, store, "alice")


def service(tmp_path, reader="alice", **settings):
    return MemoryService(Settings(
        memory_provider=settings.pop("memory_provider", "rules"), memory_store_path=str(tmp_path / "raw.db"),
        memory_lifecycle_shadow_url="http://unused", memory_lifecycle_shadow_users="alice",
        memory_lifecycle_sandbox_users="alice", memory_lifecycle_read_users=reader,
        memory_lifecycle_store_path=str(tmp_path / "ledger.db"), **settings,
    ))


@pytest.mark.parametrize("reader", ["*", "bob"])
def test_reader_requires_separate_explicit_subset(tmp_path, reader):
    with pytest.raises(ValueError, match="readers"):
        service(tmp_path, reader)


@pytest.mark.asyncio
async def test_service_read_opt_in_and_failure_do_not_return_stale_raw(tmp_path):
    store, ledger, music, _, _ = setup_history(tmp_path)
    enabled, disabled = service(tmp_path), service(tmp_path, "")
    try:
        result = await enabled.retrieve("alice", "我平时喜欢听什么音乐？")
        assert not any(r["content"] == music["content"] for r in result["evidence"])
        assert result["retrievalCoverage"]["lifecycleRead"]["activeFacts"] == 2
        old = await disabled.retrieve("alice", "轻音乐")
        assert any("芝麻团" in r["content"] and "轻音乐" in r["content"] for r in old["evidence"])
        store.forget("alice")
        failed = await enabled.retrieve("alice", "音乐")
        assert failed["status"] == "unavailable" and failed["evidence"] == []
        assert ledger.candidates("alice")  # reads do not delete audit
    finally:
        await enabled.close()
        await disabled.close()


@pytest.mark.asyncio
async def test_concurrent_revision_change_clears_answer_and_pending_candidates(tmp_path):
    _, ledger, _, _, change = setup_history(tmp_path)
    enabled = service(tmp_path)
    async def changed(*args, **kwargs):
        ledger.undo("alice", change["changeId"], enabled=True)
        return {"evidence": [{"content": "stale"}], "candidateEvidence": [{"content": "stale"}]}
    enabled._retrieve = changed
    try:
        result = await enabled.retrieve("alice", "音乐")
        assert result["status"] == "unavailable"
        assert not result["evidence"] and not result["candidateEvidence"]
    finally:
        await enabled.close()


@pytest.mark.asyncio
async def test_rerank_recent_expansion_and_sql_bridge_cannot_bypass_mask(tmp_path):
    store, _, music, _, _ = setup_history(tmp_path)
    enabled = service(tmp_path, memory_rerank_base_url="http://unused",
                      memory_provider="qwen", memory_contract_version="psych-memory-v4")
    enabled.backend = AsyncMock()
    enabled.backend.recent_messages.return_value = [
        {**r, "source": "conversation_message:1", "id": "sql:1"}
        for r in store.read_pool("alice")
    ]
    async def order(query, records):
        return records, "fixture-reranker"
    enabled.reranker.order = order
    async def retrieve(query, records, *args, **kwargs):
        assert not any(r["content"] == music["content"] for r in records)
        assert not any("芝麻团" in r["content"] and "轻音乐" in r["content"] for r in records)
        return {"evidence": records, "retrievalCoverage": {}}
    enabled._retrieve_qwen = retrieve
    try:
        result = await enabled.retrieve("alice", "音乐", recent_messages=True)
        assert result["retrievalCoverage"]["lifecycleRead"]["mode"] == "sandbox"
    finally:
        await enabled.close()


def test_long_source_hydration_and_truncated_sql_copy(tmp_path):
    store, ledger = MemoryStore(tmp_path / "raw"), MemoryLedger(tmp_path / "ledger")
    text = "我喜欢听轻音乐。" + "附加背景。" * 120
    source = job(text)
    store.ingest(source)
    ledger.apply(proposal(source, 0, 8, scope="stable"), votes(), enabled=True)
    raw = store.read_pool("alice")
    aliases = [{**r, "id": "sql", "source": "conversation_message:1"} for r in raw]
    rows = MemoryReadView(ledger, store, "alice").assemble([*raw, *aliases])
    assert len(rows) == 2
    assert all(len(r["content"]) <= 500 for r in rows)
    assert any(r["truncated"] for r in rows)


@pytest.mark.asyncio
async def test_controller_outage_fallback_uses_only_sanitized_view(tmp_path):
    setup_history(tmp_path)
    enabled = service(tmp_path, memory_provider="qwen", memory_contract_version="psych-memory-v4")
    enabled.controller.evaluate_many = AsyncMock(side_effect=httpx.ConnectError("offline"))
    try:
        result = await enabled.retrieve("alice", "我喜欢听什么音乐？")
        assert result["status"] == "lexical_fallback"
        assert any("民谣" in r["content"] for r in result["evidence"])
        assert not any("我平时喜欢听轻音乐" in r["content"] for r in result["evidence"])
    finally:
        await enabled.close()
