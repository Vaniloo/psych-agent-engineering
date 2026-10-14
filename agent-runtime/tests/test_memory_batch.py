import hashlib
import json

import httpx
import pytest
from fastapi.testclient import TestClient

from psych_agent_runtime.config import Settings
from psych_agent_runtime.decision_contract import profile_contract
from psych_agent_runtime.decision_router import LocalDecisions
from psych_agent_runtime.memory import MemoryService, MemoryStore
from psych_agent_runtime.memory_source_guard import clearly_other_person
from psych_agent_runtime.qwen_memory_contract import INSTRUCTIONS
from psych_agent_runtime.qwen_memory_service import calibrated_answer, create_app


def test_small_raw_margin_abstains_even_when_temperature_makes_probability_high():
    policy = {
        "temperature": 0.25,
        "threshold": 0.85,
        "calibrated": True,
        "enabled": True,
        "validationScope": "development",
    }
    weak = calibrated_answer(policy, 10, 11)
    assert weak["probabilities"]["yes"] > 0.98 and not weak["accepted"]
    assert weak["abstentionReason"] == "small_raw_logit_margin"
    assert calibrated_answer(policy, 10, 13)["accepted"]
    assert calibrated_answer(policy, 13, 10)["accepted"]
    assert not calibrated_answer({**policy, "enabled": False}, 10, 13)["accepted"]
    with pytest.raises(ValueError, match="non-finite"):
        calibrated_answer(policy, 0, float("nan"))


def answer(index, yes=True, probability=0.99):
    return {
        "index": index,
        "choice": "yes" if yes else "no",
        "probabilities": {
            "yes": probability if yes else 1 - probability,
            "no": 1 - probability if yes else probability,
        },
        "threshold": 0.85,
        "calibrated": True,
        "accepted": True,
    }


@pytest.mark.asyncio
async def test_batch_client_checks_source_alignment_and_identical_acceptance_rules():
    contract, questions = profile_contract("memory", "psych-memory-v4")
    client = LocalDecisions("http://batch", "test", 3, contract, questions, 0.95)
    rows = [answer(0), answer(1, probability=0.9)]
    await client.client.aclose()
    client.client = httpx.AsyncClient(
        base_url="http://batch",
        transport=httpx.MockTransport(
            lambda req: httpx.Response(
                200, json={"contractVersion": contract, "task": "relevance", "answers": rows}
            )
        ),
    )
    try:
        result = await client.evaluate_many([{}, {}], "relevance")
        assert result[0]["accepted"] and not result[1]["accepted"]
        rows.reverse()
        with pytest.raises(ValueError, match="alignment"):
            await client.evaluate_many([{}, {}], "relevance")
        rows.reverse()
        rows[1]["probabilities"]["yes"] = 0.5
        with pytest.raises(ValueError):
            await client.evaluate_many([{}, {}], "relevance")
        with pytest.raises(ValueError, match="oversized"):
            await client.evaluate_many([{}] * 17, "relevance")
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_older_worker_compatibility_keeps_validation_and_never_downgrades_auth():
    contract, questions = profile_contract("memory", "psych-memory-v4")
    client = LocalDecisions("http://old", "test", 3, contract, questions, 0.85)
    calls = []

    def handler(request):
        calls.append(request.url.path)
        if request.url.path.endswith("batch"):
            return httpx.Response(404)
        return httpx.Response(
            200,
            json={
                "contractVersion": contract,
                "answers": {"relevance": answer(0)},
                "provider": "qwen",
                "model": "old",
            },
        )

    await client.client.aclose()
    client.client = httpx.AsyncClient(base_url="http://old", transport=httpx.MockTransport(handler))
    try:
        result = await client.evaluate_many([{}, {}], "relevance")
        assert len(result) == 2 and all(r["accepted"] for r in result)
        assert client.batch_supported is False
        await client.evaluate_many([{}], "relevance")
        assert calls.count("/v1/decisions/batch") == 1
        client.batch_supported = None
        await client.client.aclose()
        client.client = httpx.AsyncClient(
            base_url="http://old", transport=httpx.MockTransport(lambda req: httpx.Response(401))
        )
        with pytest.raises(httpx.HTTPStatusError):
            await client.evaluate_many([{}], "relevance")
        assert client.batch_supported is None
    finally:
        await client.close()


def test_batch_worker_enforces_auth_contract_source_and_work_bound_without_gpu(
    tmp_path, monkeypatch
):
    report = {
        "status": "complete",
        "epochs": 1,
        "taskPolicies": {"relevance": {}},
        "datasetManifest": {
            "instructionsSha256": hashlib.sha256(
                json.dumps(INSTRUCTIONS, sort_keys=True, ensure_ascii=False).encode()
            ).hexdigest()
        },
    }
    (tmp_path / "training-report.json").write_text(json.dumps(report))
    monkeypatch.setenv("QWEN_MEMORY_API_KEY", "test")
    # No lifespan entered: invalid requests must reject before touching GPU state.
    client = TestClient(create_app(tmp_path, tmp_path))
    body = {"contractVersion": "psych-memory-v4", "task": "relevance", "states": [{}]}
    auth = {"Authorization": "Bearer test"}
    assert client.post("/v1/decisions/batch", json=body).status_code == 401
    assert (
        client.post(
            "/v1/decisions/batch", json={**body, "contractVersion": "wrong"}, headers=auth
        ).status_code
        == 409
    )
    assert client.post("/v1/decisions/batch", json=body, headers=auth).status_code == 422
    assert (
        client.post(
            "/v1/decisions/batch", json={**body, "states": [{}] * 17}, headers=auth
        ).status_code
        == 422
    )


def test_owner_scoped_expansion_is_bounded_and_preserves_original_records(tmp_path):
    store = MemoryStore(str(tmp_path / "index.db"))
    for owner in ("a", "b"):
        for i in range(30):
            store.ingest(
                {
                    "username": owner,
                    "sessionId": 1,
                    "requestId": str(i),
                    "createdAt": f"2026-10-12T12:00:{i:02d}",
                    "userMessage": f"{owner} event {i}",
                    "assistantReply": "not user fact",
                }
            )
    records = store.recent("a", 1000)
    assert len(records) == 24 and records[0]["source"] == "29"
    assert all(r["role"] == "user" and r["content"].startswith("a ") for r in records)
    assert store.recent("a", 0) == [] and store.recent("missing") == []
    store.forget("a")
    assert store.recent("a") == [] and len(store.recent("b")) == 24


@pytest.mark.parametrize(
    "query,text,blocked",
    [
        ("我每次游泳多久？", "我妹妹每次游泳二十分钟。", True),
        ("我的宠物叫什么？", "朋友的宠物叫果冻。", True),
        ("How long do I cycle?", "My brother cycles for an hour.", True),
        ("我和姐姐一起做了什么？", "姐姐陪我去图书馆。", False),
        ("我昨晚做了什么？", "姐姐陪我去图书馆。", False),
        ("我每次骑车多久？", "我姐姐骑车一小时，但我每次骑车十分钟。", False),
        ("What did I do?", "My friend called me last night.", False),
        ("我的导师叫什么？", "我的导师叫林老师。", False),
        ("我的猫叫什么？", "我的猫叫豆沙。", False),
        ("家人最近怎么样？", "我姐姐刚刚搬家。", False),
    ],
)
def test_source_boundary_leaves_relationship_and_mixed_facts_to_model(query, text, blocked):
    assert clearly_other_person(query, {"content": text}) is blocked


@pytest.mark.asyncio
async def test_semantic_expansion_keeps_lexical_candidates_and_delivered_evidence_checks(tmp_path):
    from unittest.mock import AsyncMock

    service = MemoryService(
        Settings(
            memory_provider="qwen",
            memory_contract_version="psych-memory-v4",
            memory_store_path=str(tmp_path / "index.db"),
            memory_rerank_base_url="http://rank",
        )
    )
    lexical = [{"id": str(i), "role": "user", "content": f"I ran {i} minutes."} for i in range(8)]
    extra = [
        {"id": str(i), "role": "user", "content": f"I went swimming {i} minutes."}
        for i in range(8, 32)
    ]
    service.store.candidates = lambda *a: lexical
    service.store.recent = lambda *a: extra
    service.reranker.order = AsyncMock(return_value=(extra[::-1] + lexical, "original-0.6b"))
    service.controller.evaluate_many = AsyncMock(
        return_value=[{**answer(i), "probability": 0.99} for i in range(12)]
    )
    service.controller.evaluate = AsyncMock(
        return_value={"decisions": {"evidence_sufficient": answer(0)}}
    )
    try:
        result = await service.retrieve("a", "How long did I run?")
        inputs = service.controller.evaluate_many.call_args.args[0]
        assert len(inputs) == 12 and all(
            any(s["candidate"]["content"] == c["content"] for s in inputs) for c in lexical
        )
        assert result["status"] == "controlled" and len(result["evidence"]) == 6
        assert result["rerank"]["semanticAdded"] == 4
        assert len(service.reranker.order.call_args.args[1]) == 32
        service.reranker.order.side_effect = httpx.ConnectError("offline")
        service.controller.evaluate_many.side_effect = httpx.ConnectError("offline")
        result = await service.retrieve("a", "How long did I run?")
        assert result["status"] == "lexical_fallback" and result["evidence"] == lexical[:6]
        assert result["rerank"]["status"] == "fallback"
    finally:
        await service.close()


@pytest.mark.asyncio
async def test_truncated_sources_and_obvious_other_subject_do_not_claim_sufficiency(tmp_path):
    from unittest.mock import AsyncMock

    service = MemoryService(
        Settings(
            memory_provider="qwen",
            memory_contract_version="psych-memory-v4",
            memory_store_path=str(tmp_path / "index.db"),
        )
    )
    records = [
        {"id": "x", "role": "user", "content": "我朋友每次跑步半小时。"},
        {"id": "y", "role": "user", "content": "我跑步十分钟。", "truncated": True},
    ]
    service.store.candidates = lambda *a: records
    service.controller.evaluate_many = AsyncMock(return_value=[{**answer(0), "probability": 0.99}])
    service.controller.evaluate = AsyncMock(
        return_value={"decisions": {"evidence_sufficient": answer(0)}}
    )
    try:
        result = await service.retrieve("a", "我每次跑步多久？")
        assert result["sourceGuard"]["otherPersonBlocked"] == 1
        assert [r["id"] for r in result["evidence"]] == ["y"] and result["status"] == "controlled"
    finally:
        await service.close()
