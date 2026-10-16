import copy
import sqlite3
from unittest.mock import AsyncMock

import pytest

from psych_agent_runtime.config import Settings
from psych_agent_runtime.memory import MemoryService
from psych_agent_runtime.memory_facts import attribute_relation, fact_spans
from psych_agent_runtime.memory_ledger import MemoryLedger, proposal
from psych_agent_runtime.memory_projection import MemoryProjection
from psych_agent_runtime.memory_shadow import POLICY_VERSION, MemoryLifecycleShadow


def job(text, request="r1", owner="alice"):
    return {
        "username": owner,
        "sessionId": 1,
        "requestId": request,
        "userMessage": text,
        "assistantReply": "收到",
        "createdAt": "2026-10-16T12:00:00+08:00",
    }


def item(text, request="r1", owner="alice"):
    source = job(text, request, owner)
    return proposal(source, 0, len(text), scope="stable")


def votes(**changes):
    values = {
        "write_worthy": "yes",
        "grounded": "yes",
        "duplicate": "no",
        "supersedes": "no",
        "conflict": "no",
    }
    values.update(changes)
    return {k: {"choice": v, "accepted": True} for k, v in values.items()}


def response(tasks, **changes):
    return {
        "model": "frozen-v2",
        "decisions": {k: v for k, v in votes(**changes).items() if k in tasks},
    }


def test_extractive_spans_preserve_decimal_and_keep_correction_context():
    text = "我平时喜欢听轻音乐，我今天有点难过。"
    spans = fact_spans(text)
    assert len(spans) == 2
    assert text[spans[0][0] : spans[0][1]] == "我平时喜欢听轻音乐"
    assert text[spans[1][0] : spans[1][1]] == "我今天有点难过。"
    assert spans[0][2] == "stable" and spans[1][2] == "temporary"
    text = "I usually run 1.5 km. My cat is quiet."
    assert "1.5" in text[fact_spans(text)[0][0] : fact_spans(text)[0][1]]
    text = "更正：我喜欢听的是民谣，不是轻音乐。"
    assert fact_spans(text) == [(0, len(text), "stable")]


def test_attribute_boundary_is_narrow_and_does_not_infer_unknown_slots():
    assert (
        attribute_relation("更正：我平时喜欢听的是民谣，不是轻音乐。", "我唯一一只猫叫白白。")
        == "different"
    )
    assert attribute_relation("我的兔子叫果果。", "我唯一的兔子叫团团。") == "same"
    assert attribute_relation("我的职业是教师。", "我喜欢听钢琴曲。") == "different"
    assert attribute_relation("我朋友的猫叫白白。", "我的猫叫黑黑。") == "unknown"
    assert attribute_relation("我喜欢画画。", "我喜欢园艺。") == "unknown"


@pytest.mark.asyncio
async def test_cross_attribute_replacement_is_filtered_then_undo_restores_only_music(tmp_path):
    ledger = MemoryLedger(tmp_path / "ledger.db")
    old = item("我平时喜欢听轻音乐。", "old-music")
    pet = item("我唯一一只兔子叫白白。", "pet")
    ledger.apply(old, votes(), enabled=True)
    ledger.apply(pet, votes(), enabled=True)
    shadow = MemoryLifecycleShadow(ledger, "http://unused", "fixture")
    shadow.controller.evaluate = AsyncMock(
        side_effect=[
            response(["write_worthy", "grounded"]),
            response(["duplicate", "supersedes", "conflict"], supersedes="yes", conflict="yes"),
        ]
    )
    source = job("更正：我平时喜欢听的是民谣，不是轻音乐。")
    try:
        preview = await shadow.inspect(source)
        row = preview["proposals"][0]
        assert row["action"] == "replace" and row["targetId"] == old["id"]
        assert row["excludedAttributes"] == 1
        assert shadow.controller.evaluate.await_count == 2
        assert len(ledger.candidates("alice")) == 2  # inspect itself is read-only
        result = MemoryProjection(ledger, {"alice"}).project(source, preview)[0]
        assert result["applied"] is True
        assert ledger.get("alice", old["id"])["status"] == "superseded"
        ledger.undo("alice", result["changeId"], enabled=True)
        assert ledger.get("alice", old["id"])["status"] == "active"
        assert ledger.get("alice", old["id"])["version"] == 3
        assert ledger.get("alice", pet["id"])["version"] == 1
    finally:
        await shadow.close()


@pytest.mark.asyncio
async def test_each_atomic_value_check_is_local_but_grounding_keeps_full_message(tmp_path):
    ledger = MemoryLedger(tmp_path / "ledger.db")
    shadow = MemoryLifecycleShadow(ledger, "http://unused", "fixture")
    source = job("我平时喜欢听轻音乐，我今天有点难过。")

    async def score(state, tasks):
        if tasks == ["write_worthy"]:
            return response(
                tasks, write_worthy="yes" if "喜欢" in state["source"]["content"] else "no"
            )
        assert tasks == ["grounded"] and state["source"]["content"] == source["userMessage"]
        return response(tasks)

    shadow.controller.evaluate = score
    try:
        preview = await shadow.inspect(source)
        assert [p["action"] for p in preview["proposals"]] == ["add", "skip"]
        assert ledger.candidates("alice") == []
        result = MemoryProjection(ledger, {"alice"}).project(source, preview)
        assert sum(p.get("applied", False) for p in result) == 1
        assert "难过" not in ledger.candidates("alice")[0]["content"]
    finally:
        await shadow.close()


@pytest.mark.parametrize("action", ["add", "support", "replace"])
def test_undo_is_owned_idempotent_and_does_not_reenable_old_versions(tmp_path, action):
    ledger = MemoryLedger(tmp_path / "ledger.db")
    base = item("我平时喜欢听轻音乐。", "base")
    ledger.apply(base, votes(), enabled=True)
    old = ledger.get("alice", base["id"])
    candidate = (
        item("我的职业是教师。", "new")
        if action == "add"
        else item(
            base["content"]
            if action == "support"
            else "更正：我现在喜欢听古典音乐，不再喜欢轻音乐。",
            "new",
        )
    )
    v = (
        votes(duplicate="yes")
        if action == "support"
        else votes(supersedes="yes", conflict="yes")
        if action == "replace"
        else votes()
    )
    result = ledger.apply(
        candidate, v, target=old if action != "add" else None, expected_version=1, enabled=True
    )
    assert result["applied"]
    with pytest.raises(PermissionError):
        ledger.undo("alice", result["changeId"])
    with pytest.raises(ValueError, match="foreign"):
        ledger.undo("bob", result["changeId"], enabled=True)
    ledger.undo("alice", result["changeId"], enabled=True)
    revision = ledger.revision("alice")
    assert ledger.undo("alice", result["changeId"], enabled=True)["action"] == "undo_replay"
    assert ledger.revision("alice") == revision
    assert [r["id"] for r in ledger.candidates("alice")] == [base["id"]]
    assert ledger.apply(candidate, v, enabled=True)["sourceStatus"] == "reverted"


def test_undo_rejects_intervening_support_atomically_and_cross_attribute_replace(tmp_path):
    ledger = MemoryLedger(tmp_path / "ledger.db")
    base = item("我的猫叫灰灰。", "base")
    created = ledger.apply(base, votes(), enabled=True)
    old = ledger.get("alice", base["id"])
    refused = ledger.apply(
        item("我喜欢听轻音乐。", "wrong"),
        votes(supersedes="yes"),
        target=old,
        expected_version=1,
        enabled=True,
    )
    assert refused["reason"] == "attribute_not_verified"
    ledger.apply(
        item(base["content"], "support"),
        votes(duplicate="yes"),
        target=old,
        expected_version=1,
        enabled=True,
    )
    before = ledger.revision("alice")
    with pytest.raises(ValueError, match="later changes"):
        ledger.undo("alice", created["changeId"], enabled=True)
    assert ledger.revision("alice") == before


def test_old_ledger_source_schema_migrates_without_losing_rows(tmp_path):
    path = tmp_path / "old.db"
    with sqlite3.connect(path) as db:
        db.execute(
            "CREATE TABLE memory_sources (owner TEXT,item_id TEXT,proposal_id TEXT,request_id TEXT,session_id INTEGER,span_start INTEGER,span_end INTEGER,source_hash TEXT,PRIMARY KEY(owner,proposal_id))"
        )
        db.execute("INSERT INTO memory_sources VALUES ('alice','old','p','r',1,0,4,'hash')")
    ledger = MemoryLedger(path)
    with ledger.connect() as db:
        row = db.execute("SELECT * FROM memory_sources").fetchone()
    assert row["item_id"] == "old" and row["status"] == "active"


@pytest.mark.asyncio
@pytest.mark.parametrize("tamper", ["source", "decisions", "revision", "owner", "missing_audit"])
async def test_projection_rejects_unbound_or_stale_plans(tmp_path, tamper):
    ledger = MemoryLedger(tmp_path / "ledger.db")
    shadow = MemoryLifecycleShadow(ledger, "http://unused", "fixture")
    shadow.controller.evaluate = AsyncMock(return_value=response(["write_worthy", "grounded"]))
    source = job("我喜欢听轻音乐。")
    try:
        preview = copy.deepcopy(await shadow.inspect(source))
        if tamper == "source":
            source["userMessage"] = "我喜欢听重音乐。"
        if tamper == "decisions":
            preview["proposals"][0]["decisions"]["grounded"]["choice"] = "no"
        if tamper == "revision":
            ledger.apply(item("我的职业是教师。", "other"), votes(), enabled=True)
        if tamper == "owner":
            source["username"] = "bob"
        if tamper == "missing_audit":
            with ledger.connect() as db:
                db.execute("DELETE FROM memory_shadow")
        before = ledger.revision("alice")
        result = MemoryProjection(ledger, {"alice"}).project(source, preview)
        assert not any(r.get("applied") for r in result)
        assert ledger.revision("alice") == before and ledger.candidates("bob") == []
    finally:
        await shadow.close()


@pytest.mark.parametrize(
    "sandbox,shadow,same_path",
    [("*", "alice", False), ("bob", "alice", False), ("alice", "alice", True)],
)
def test_projection_configuration_cannot_expand_users_or_share_raw_index(
    tmp_path, sandbox, shadow, same_path
):
    raw = tmp_path / "raw.db"
    with pytest.raises(ValueError):
        MemoryService(
            Settings(
                memory_provider="rules",
                memory_store_path=str(raw),
                memory_lifecycle_shadow_url="http://unused",
                memory_lifecycle_shadow_users=shadow,
                memory_lifecycle_sandbox_users=sandbox,
                memory_lifecycle_store_path=str(raw if same_path else tmp_path / "ledger.db"),
            )
        )
    if raw.exists():
        with sqlite3.connect(raw) as db:
            assert not db.execute(
                "SELECT 1 FROM sqlite_master WHERE name='memory_changes'"
            ).fetchone()


@pytest.mark.asyncio
async def test_outbox_projection_is_separate_and_requires_its_own_user_allowlist(tmp_path):
    service = MemoryService(
        Settings(
            memory_provider="rules",
            memory_store_path=str(tmp_path / "raw.db"),
            memory_lifecycle_shadow_url="http://unused",
            memory_lifecycle_shadow_users="alice,bob",
            memory_lifecycle_sandbox_users="alice",
            memory_lifecycle_store_path=str(tmp_path / "ledger.db"),
        )
    )
    service.shadow.controller.evaluate = AsyncMock(
        return_value=response(["write_worthy", "grounded"])
    )
    try:
        await service.start()
        for owner in ("alice", "bob"):
            await service.ingest(job("我喜欢听轻音乐。", owner=owner))
        await service.shadow_queue.join()
        assert len(service.shadow.ledger.candidates("alice")) == 1
        assert service.shadow.ledger.candidates("bob") == []
        assert len(service.store.recent("alice")) == len(service.store.recent("bob")) == 1
        assert service.lifecycle_info()["formalWritesEnabled"] is False
        assert service.lifecycle_info()["projection"]["appliedChanges"] == 1
        assert service.lifecycle_info()["policyVersion"] == POLICY_VERSION
    finally:
        await service.close()


def test_unknown_replacement_and_mixed_attribute_targets_remain_reviewable(tmp_path):
    assert attribute_relation("我的兔子叫圆圆，也喜欢听轻音乐。", "我的兔子叫团团。") == "unknown"
    ledger = MemoryLedger(tmp_path / "ledger.db")
    original = item("我喜欢画画。", "original")
    ledger.apply(original, votes(), enabled=True)
    old = ledger.get("alice", original["id"])
    result = ledger.apply(
        item("更正：我喜欢园艺。", "correction"),
        votes(supersedes="yes"),
        target=old,
        expected_version=old["version"],
        enabled=True,
    )
    assert not result["applied"] and result["reason"] == "attribute_not_verified"
    assert ledger.get("alice", original["id"]) == old


def test_concurrent_add_plans_cannot_both_commit_the_same_revision(tmp_path):
    from concurrent.futures import ThreadPoolExecutor

    ledger = MemoryLedger(tmp_path / "ledger.db")
    revision = ledger.revision("alice")

    def commit(i):
        try:
            return ledger.apply(
                item(f"我的猫叫团{i}。", f"concurrent-{i}"),
                votes(),
                expected_revision=revision,
                enabled=True,
            )["applied"]
        except ValueError:
            return False

    with ThreadPoolExecutor(max_workers=2) as pool:
        result = list(pool.map(commit, range(2)))
    assert sorted(result) == [False, True]
    assert len(ledger.candidates("alice")) == 1
    with ledger.connect() as db:
        assert db.execute("SELECT count(*) FROM memory_sources").fetchone()[0] == 1
        assert db.execute("SELECT count(*) FROM memory_changes").fetchone()[0] == 1


def test_undo_to_empty_does_not_restore_an_old_plan_revision(tmp_path):
    ledger = MemoryLedger(tmp_path / "ledger.db")
    original = ledger.revision("alice")
    other_owner = ledger.revision("bob")
    first = ledger.apply(
        item("我的猫叫白白。", "first"), votes(), expected_revision=original, enabled=True
    )
    ledger.undo("alice", first["changeId"], enabled=True)
    assert ledger.candidates("alice") == [] and ledger.revision("alice") != original
    assert ledger.revision("bob") == other_owner
    with pytest.raises(ValueError, match="stale ledger"):
        ledger.apply(
            item("我的职业是教师。", "stale-empty-plan"),
            votes(),
            expected_revision=original,
            enabled=True,
        )
    assert ledger.candidates("alice") == []
