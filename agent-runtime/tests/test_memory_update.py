import asyncio
from unittest.mock import AsyncMock

import pytest

from psych_agent_runtime.config import Settings
from psych_agent_runtime.memory import MemoryService, MemoryStore
from psych_agent_runtime.memory_update import projection_status
from tests.test_memory_projection import job


def test_delivery_is_owner_scoped_durable_bounded_and_forgotten(tmp_path):
    path = tmp_path / "raw.db"
    store = MemoryStore(path)
    source = job("我的职业是教师。")
    assert store.delivery_status("alice", "r1") == {"status": "pending", "recentReady": False}
    store.ingest(source)
    assert store.delivery_status("alice", "r1")["status"] == "unconfirmed"  # legacy, not a success
    store.update_receipt(source, {"status": "updated", "changedCount": 1})
    assert MemoryStore(path).delivery_status("alice", "r1")["changedCount"] == 1
    assert store.delivery_status("bob", "r1")["recentReady"] is False
    for i in range(201):
        store.update_receipt(job("no text in receipt", f"other-{i}"), {"status": "indexed"})
    with store.connect() as db:
        assert db.execute("SELECT count(*) FROM memory_updates").fetchone()[0] == 200
    store.forget("alice")
    assert store.update_status("alice", "other-200") is None


def test_abandoned_pending_does_not_claim_success(tmp_path):
    store = MemoryStore(tmp_path / "raw")
    store.ingest(job("我喜欢音乐。"))
    store.update_receipt(job("我喜欢音乐。"), {"status": "pending"})
    with store.connect() as db:
        db.execute("UPDATE memory_updates SET updated_at='2020-01-01T00:00:00+00:00'")
    assert store.delivery_status("alice", "r1")["status"] == "unconfirmed"


def test_new_observation_and_delivery_receipt_commit_atomically(tmp_path, monkeypatch):
    store = MemoryStore(tmp_path / "raw")
    source = job("我每天骑车上班。")
    original = store._record_receipt
    def failed(*args, **kwargs):
        raise RuntimeError("receipt disk failure")
    monkeypatch.setattr(store, "_record_receipt", failed)
    with pytest.raises(RuntimeError):
        store.ingest(source, delivery={"status": "pending"})
    assert store.read_pool("alice") == []
    assert store.update_status("alice", "r1") is None
    monkeypatch.setattr(store, "_record_receipt", original)
    assert store.ingest(source, delivery={"status": "pending"}) is True
    assert store.delivery_status("alice", "r1")["status"] == "pending"
    store.update_receipt(source, {"status": "updated"})
    assert store.ingest(source, delivery={"status": "pending"}) is False
    assert store.delivery_status("alice", "r1")["status"] == "updated"


@pytest.mark.parametrize(("preview", "projected", "status"), [
    ({}, [{"action": "add", "applied": True}], "updated"),
    ({}, [{"action": "replace", "applied": True}, {"action": "review"}], "partial"),
    ({"proposals": [{"previewError": "Timeout"}]}, [{"action": "review"}], "unconfirmed"),
    ({}, [{"action": "review"}], "review"),
    ({}, [{"action": "keep_separate"}], "review"),
    ({}, [{"action": "replay", "applied": False}], "review"),
    ({"reason": "retention_optout"}, [], "unchanged"),
    ({}, [{"action": "skip", "applied": False}], "unchanged"),
])
def test_only_real_projection_changes_report_updated(preview, projected, status):
    assert projection_status(preview, projected)["status"] == status


@pytest.mark.asyncio
async def test_normal_outbox_receipt_is_recent_only_and_replay_preserves_completion(tmp_path):
    service = MemoryService(Settings(memory_store_path=str(tmp_path / "raw")))
    try:
        source = job("我今天很开心。")
        await service.ingest(source)
        assert service.store.delivery_status("alice", "r1")["status"] == "indexed"
        await service.ingest(source)
        assert service.store.delivery_status("alice", "r1")["status"] == "indexed"
    finally:
        await service.close()


@pytest.mark.asyncio
async def test_projection_receipt_follows_worker_result_not_outbox_ack(tmp_path):
    service = MemoryService(Settings(memory_store_path=str(tmp_path / "raw"),
        memory_lifecycle_shadow_url="http://unused", memory_lifecycle_shadow_users="alice",
        memory_lifecycle_sandbox_users="alice", memory_lifecycle_store_path=str(tmp_path / "ledger")))
    service.shadow.inspect = AsyncMock(return_value={"proposals": [{"action": "review"}]})
    service.projection.project = lambda *args: [{"action": "review", "applied": False}]
    try:
        await service.ingest(job("我的职业是教师。"))
        assert service.store.delivery_status("alice", "r1")["status"] == "pending"
        await service.start()
        await asyncio.wait_for(service.shadow_queue.join(), timeout=2)
        assert service.store.delivery_status("alice", "r1")["status"] == "review"
        await service.ingest(job("我的职业是教师。"))
        assert service.shadow_queue.qsize() == 0
        assert service.store.delivery_status("alice", "r1")["status"] == "review"
    finally:
        await service.close()
