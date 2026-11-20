import asyncio
from unittest.mock import AsyncMock

import pytest

from psych_agent_runtime.config import Settings
from psych_agent_runtime.decision_contract import (
    MEMORY_CONTRACT,
    MEMORY_EVIDENCE_CONTRACT,
    MEMORY_PAIR_CONTRACT,
    memory_evidence_state,
    memory_pair_state,
    profile_contract,
)
from psych_agent_runtime.memory import MemoryService


def test_pair_serving_ignores_tracking_fields_without_losing_source():
    state = {
        "query": "我哪天游泳？",
        "candidate": {
            "role": "user",
            "content": "每周四。",
            "id": "tracking-only",
            "sessionId": 77,
        },
    }
    normalized = memory_pair_state(state)
    assert normalized["candidate"] == {"role": "user", "content": "每周四。"}
    assert "tracking-only" not in str(normalized)
    with pytest.raises(ValueError, match="source"):
        memory_pair_state({"query": "q", "candidate": {"content": "fact"}})
    assert profile_contract("memory")[0] == MEMORY_CONTRACT
    with pytest.raises(ValueError, match="unsupported"):
        profile_contract("router", MEMORY_PAIR_CONTRACT)








def test_pair_retrieval_preserves_original_evidence_ids_and_bounded_rounds(tmp_path):
    async def run():
        service = MemoryService(
            Settings(
                memory_provider="laya",
                memory_contract_version=MEMORY_PAIR_CONTRACT,
                memory_store_path=str(tmp_path / "index.sqlite3"),
            )
        )
        candidates = [
            {"id": "kept", "role": "user", "content": "周四游泳", "sessionId": 1},
            {"id": "discarded", "role": "user", "content": "周一买水果", "sessionId": 2},
        ]
        service.store.candidates = lambda *args: candidates

        def decision(state, tasks):
            if tasks == ["relevance"]:
                assert set(state) == {"query", "candidate"}
                assert set(state["candidate"]) == {"role", "content"}
                yes = "游泳" in state["candidate"]["content"]
                return {
                    "decisions": {
                        "relevance": {
                            "choice": "yes" if yes else "no",
                            "probability": 0.99,
                            "accepted": True,
                        }
                    }
                }
            assert state["evidence"][0]["id"] == "kept"
            return {"decisions": {"evidence_sufficient": {"choice": "yes", "accepted": True}}}

        service.controller.evaluate = AsyncMock(side_effect=decision)
        try:
            result = await service.retrieve("alice", "游泳")
            assert result["status"] == "sufficient" and result["rounds"] == 1
            assert result["evidence"] == candidates[:1]
            assert service.controller.evaluate.call_count == 3
        finally:
            await service.close()

    asyncio.run(run())


@pytest.mark.parametrize("contract", [MEMORY_PAIR_CONTRACT, MEMORY_EVIDENCE_CONTRACT])
def test_worker_loads_versioned_contract_and_normalizes_before_forward(
    tmp_path, monkeypatch, contract
):
    import hashlib
    import json
    import sys
    from types import SimpleNamespace

    from fastapi.testclient import TestClient

    from psych_agent_runtime.routing_service import create_app, questions_digest

    seen = []
    version, questions = profile_contract("memory", contract)
    weights = b"contract-test-not-real-weights"
    (tmp_path / "model.safetensors").write_bytes(weights)
    metadata = {
        "profile": "memory",
        "contractVersion": version,
        "amp": "fp16",
        "questionsSha256": questions_digest(questions),
        "weightsSha256": hashlib.sha256(weights).hexdigest(),
        "maxStateTokens": 1008,
        "model": "pair-fixture",
        "tasks": {
            t: {"enabled": True, "calibrated": True, "temperature": 1.0, "threshold": 0.5}
            for t in questions
        },
    }
    (tmp_path / "deployment.json").write_text(json.dumps(metadata))

    def predict(state, tasks):
        seen.append(state)
        return {
            "answers": {
                t: {"choice": "yes", "probabilities": {"yes": 0.99, "no": 0.01}} for t in tasks
            }
        }

    agent = SimpleNamespace(
        device="cpu",
        cfg={"max_len": 1536, "head_max_len": 512},
        tok=SimpleNamespace(mask_token="<mask>", encode=lambda *a, **k: [1]),
        predict=predict,
    )
    monkeypatch.setitem(sys.modules, "laya", SimpleNamespace(load=lambda *a, **k: agent))
    monkeypatch.setitem(sys.modules, "torch", SimpleNamespace(set_num_threads=lambda n: None))
    monkeypatch.setenv("DECISION_PROFILE", "memory")
    monkeypatch.setenv("DECISION_CHECKPOINT", str(tmp_path))
    monkeypatch.setenv("ROUTER_DEVICE", "cpu")
    monkeypatch.delenv("DECISION_CONTRACT", raising=False)
    monkeypatch.setenv("DECISION_API_KEY", "test-key")
    headers = {"Authorization": "Bearer test-key"}
    with TestClient(create_app()) as client:
        assert client.get("/health").json()["contractVersion"] == version
        state = {
            "query": "什么时候？",
            "candidate": {"role": "user", "content": "周四", "id": "tracking"},
        }
        payload = {"contractVersion": version, "state": state, "tasks": ["relevance"]}
        assert client.post("/v1/decisions", json=payload).status_code == 401
        assert client.post("/v1/decisions", json=payload, headers=headers).status_code == 200
        assert seen == [memory_pair_state(state)]
        payload["contractVersion"] = MEMORY_CONTRACT
        assert client.post("/v1/decisions", json=payload, headers=headers).status_code == 409
        payload["contractVersion"] = version
        payload["tasks"] = ["relevance", "semantic"]
        assert client.post("/v1/decisions", json=payload, headers=headers).status_code == 422
        if contract == MEMORY_EVIDENCE_CONTRACT:
            state = {
                "query": "什么时候？",
                "evidence": [{"role": "user", "content": "周四", "id": "tracking"}],
            }
            payload.update(state=state, tasks=["evidence_sufficient"])
            assert client.post("/v1/decisions", json=payload, headers=headers).status_code == 200
            assert seen[-1] == memory_evidence_state(state)
            payload["tasks"] = ["evidence_sufficient", "semantic"]
            assert client.post("/v1/decisions", json=payload, headers=headers).status_code == 422
            payload["tasks"] = ["evidence_sufficient"]
            payload["state"]["evidence"][0].pop("role")
            assert client.post("/v1/decisions", json=payload, headers=headers).status_code == 422
