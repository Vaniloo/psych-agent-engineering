import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from psych_agent_runtime.config import Settings
from psych_agent_runtime.decision_contract import (
    MEMORY_EVIDENCE_CONTRACT,
    MEMORY_PAIR_CONTRACT,
    memory_evidence_state,
    profile_contract,
)
from psych_agent_runtime.memory import MemoryService
from psych_agent_runtime.memory_head import MemoryHeadAdapter
from tests.test_memory_adapter import Model, Tensor


def test_sufficiency_normalization_and_source_validation():
    state = {
        "query": "几次和多久？",
        "evidence": [{"role": "user", "content": "两次，半小时。", "source": "request", "id": "x"}],
    }
    normalized = memory_evidence_state(state)
    assert normalized["evidence"] == [{"role": "user", "content": "两次，半小时。"}]
    assert memory_evidence_state({"query": "q", "evidence": []})["evidence"] == []
    with pytest.raises(ValueError, match="source"):
        memory_evidence_state({"query": "q", "evidence": [{"content": "fact"}]})
    with pytest.raises(ValueError, match="eight"):
        memory_evidence_state({"query": "q", "evidence": [{"role": "user", "content": "fact"}] * 9})
    assert profile_contract("memory", MEMORY_PAIR_CONTRACT)[0] == MEMORY_PAIR_CONTRACT




def test_two_full_task_adapters_restore_base_on_each_task_and_exception():
    model = Model()

    def predict(state, questions):
        if state.get("fail"):
            raise RuntimeError("failed")
        return tuple(v.value for v in model.values.values())

    adapter = MemoryHeadAdapter(
        SimpleNamespace(model=model, predict=predict),
        {"encoder.weight": Tensor(10), "head.weight": Tensor(20)},
        full=True,
        task_states={
            "evidence_sufficient": {"encoder.weight": Tensor(30), "head.weight": Tensor(40)}
        },
    )
    assert adapter.predict({}, {"relevance": {}}) == (10, 20)
    assert adapter.predict({}, {"evidence_sufficient": {}}) == (30, 40)
    assert adapter.predict({}, {"semantic": {}}) == (1, 2)
    with pytest.raises(RuntimeError):
        adapter.predict({"fail": True}, {"evidence_sufficient": {}})
    assert adapter.predict({}, {"relevance": {}}) == (10, 20)
    assert adapter.predict({}, {"preference": {}}) == (1, 2)


def test_v4_memory_service_keeps_pair_scoring_and_sufficiency_separate(tmp_path):
    async def run():
        service = MemoryService(
            Settings(
                memory_provider="laya",
                memory_contract_version=MEMORY_EVIDENCE_CONTRACT,
                memory_store_path=str(tmp_path / "index.db"),
            )
        )
        service.store.candidates = lambda *args: [
            {"id": "x", "role": "user", "content": "周四游泳", "source": "request"}
        ]
        calls = []

        def evaluate(state, tasks):
            calls.append((state, tasks))
            return {
                "decisions": {tasks[0]: {"choice": "yes", "accepted": True, "probability": 0.99}}
            }

        service.controller.evaluate = AsyncMock(side_effect=evaluate)
        try:
            result = await service.retrieve("owner", "哪天游泳？")
            assert result["status"] == "sufficient" and result["evidence"][0]["id"] == "x"
            assert [t for s, t in calls] == [["relevance"], ["evidence_sufficient"]]
            assert "id" not in calls[0][0]["candidate"]
            assert calls[1][0]["evidence"][0]["id"] == "x"
        finally:
            await service.close()

    asyncio.run(run())
