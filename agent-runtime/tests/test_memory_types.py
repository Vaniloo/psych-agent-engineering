import json

import pytest

from psych_agent_runtime.memory import MemoryStore
from psych_agent_runtime.memory_scope import merge_sources
from psych_agent_runtime.memory_types import accepted_types, diversify


def tags(*names):
    return {name: {"choice": "yes", "accepted": True} for name in names}


@pytest.mark.parametrize(
    "raw",
    [
        None,
        "bad json",
        [],
        {"preference": {"choice": "yes", "accepted": False}},
        {"preference": {"choice": "no", "accepted": True}},
    ],
)
def test_unknown_rejected_and_negative_labels_never_authorize_typed_recall(raw):
    assert accepted_types(raw) == []


def test_multilabel_support_is_preserved_without_turning_it_into_write_permission():
    assert accepted_types(json.dumps(tags("semantic", "preference"))) == ["semantic", "preference"]


def test_diversity_keeps_global_budget_and_recovers_underrepresented_relevant_type():
    rows = [
        {"id": str(i), "content": "焦虑", "memoryTypes": ["preference", "semantic"]}
        for i in range(8)
    ]
    rows += [
        {
            "id": "method",
            "content": "我焦虑时先写下担心的事情再逐项检查。",
            "memoryTypes": ["procedural"],
        },
        {"id": "unrelated", "content": "我昨天去超市买菜了。", "memoryTypes": ["episodic"]},
    ]
    selected = diversify(rows, "焦虑", rows[:8], 8)
    assert len(selected) == 8
    assert selected[:6] == rows[:6]
    assert any(r["id"] == "method" for r in selected)
    assert not any(r["id"] == "unrelated" for r in selected)
    # Losing all tags leaves the original global search result intact.
    untyped = [{**r, "memoryTypes": []} for r in rows]
    assert diversify(untyped, "焦虑", untyped[:8], 8) == untyped[:8]


def test_sql_labels_owner_and_session_scope_survive_retrieval(tmp_path):
    store = MemoryStore(str(tmp_path / "memory.db"))
    for owner, session in [("alice", 1), ("alice", 2), ("bob", 3)]:
        store.ingest(
            {
                "username": owner,
                "sessionId": session,
                "requestId": str(session),
                "userMessage": "我喜欢音乐。",
                "assistantReply": "好。",
                "createdAt": "2026-10-19T12:00:00+08:00",
            },
            tags("semantic", "preference"),
        )
    rows = store.candidates("alice", "音乐", exclude_session_id=1, type_recall=True)
    assert len(rows) == 1 and rows[0]["sessionId"] == 2
    assert rows[0]["memoryTypes"] == ["semantic", "preference"]


def test_recent_bridge_preserves_tags_only_for_complete_identical_same_session_sources():
    raw = {
        "id": "raw",
        "role": "user",
        "sessionId": 1,
        "content": "我喜欢音乐。",
        "source": "request",
        "observedAt": "2026-10-19T12:00:00+08:00",
        "memoryTypes": ["preference"],
    }
    fresh = {
        **raw,
        "id": "sql",
        "source": "sql",
        "observedAt": "2026-10-19T12:00:01+08:00",
        "memoryTypes": [],
    }
    result = merge_sources([raw, fresh])
    assert len(result) == 1 and result[0]["id"] == "sql"
    assert result[0]["memoryTypes"] == ["preference"]
    assert fresh["memoryTypes"] == []
    assert not merge_sources([{**raw, "truncated": True}, fresh])[0]["memoryTypes"]
    assert len(merge_sources([raw, {**fresh, "sessionId": 2}])) == 2


@pytest.mark.asyncio
async def test_controlled_memory_requires_read_scope_and_pinned_artifact(tmp_path):
    from psych_agent_runtime.config import Settings
    from psych_agent_runtime.memory import MemoryService

    settings = {
        "memory_store_path": str(tmp_path / "raw"),
        "memory_lifecycle_shadow_url": "http://unused",
        "memory_lifecycle_shadow_users": "alice",
        "memory_lifecycle_sandbox_users": "alice",
        "memory_lifecycle_read_users": "alice",
        "memory_lifecycle_store_path": str(tmp_path / "ledger"),
        "memory_lifecycle_expected_sha": "pinned",
        "memory_source_review_enabled": True,
        "ai_provider": "ollama",
    }
    with pytest.raises(ValueError, match="active memory"):
        MemoryService(Settings(**settings, memory_lifecycle_active_users="bob"))
    with pytest.raises(ValueError, match="active memory"):
        MemoryService(
            Settings(
                **{**settings, "memory_lifecycle_expected_sha": ""},
                memory_lifecycle_active_users="alice",
            )
        )
    service = MemoryService(Settings(**settings, memory_lifecycle_active_users="alice"))
    try:
        assert service.lifecycle_info()["mode"] == "controlled"
        assert service.lifecycle_info()["formalWritesEnabled"] is True
        assert service.lifecycle_info()["activeUsers"] == 1
    finally:
        await service.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("damage", ["sha", "contract", "outage"])
async def test_wrong_or_unavailable_lifecycle_worker_cannot_authorize_a_write(tmp_path, damage):
    import httpx

    from psych_agent_runtime.memory_ledger import MemoryLedger
    from psych_agent_runtime.memory_shadow import MemoryLifecycleShadow

    shadow = MemoryLifecycleShadow(
        MemoryLedger(tmp_path / "ledger"), "http://worker", "test", expected_sha="pinned"
    )
    await shadow.controller.client.aclose()

    def response(request):
        assert request.url.path == "/health"
        return httpx.Response(
            503 if damage == "outage" else 200,
            json={
                "ready": True,
                "contractVersion": "wrong" if damage == "contract" else "psych-memory-lifecycle-v1",
                "artifact": {"adapterSha256": "wrong" if damage == "sha" else "pinned"},
            },
        )

    shadow.controller.client = httpx.AsyncClient(
        base_url="http://worker", transport=httpx.MockTransport(response)
    )
    try:
        preview = await shadow.inspect(
            {
                "username": "alice",
                "sessionId": 1,
                "requestId": "test",
                "userMessage": "我的职业是教师。",
                "createdAt": "2026-10-19T12:00:00+08:00",
            }
        )
        assert preview["proposals"][0]["action"] == "review"
        assert preview["proposals"][0]["previewError"]
        assert shadow.ledger.candidates("alice") == []
    finally:
        await shadow.close()
