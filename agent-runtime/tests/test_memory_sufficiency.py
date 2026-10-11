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
from training.decision_data import prepare_row
from training.refine_sufficiency import fixtures


def test_sufficiency_normalization_training_serving_and_source_validation():
    state = {
        "query": "几次和多久？",
        "evidence": [{"role": "user", "content": "两次，半小时。", "source": "request", "id": "x"}],
    }
    row = {
        "contractVersion": MEMORY_EVIDENCE_CONTRACT,
        "state": state,
        "expected": {"evidence_sufficient": "yes"},
        "reviewed": True,
        "splitGroup": "owned-fact",
    }
    normalized = memory_evidence_state(state)
    assert prepare_row(row, "memory", contract=MEMORY_EVIDENCE_CONTRACT)["state"] == normalized
    assert normalized["evidence"] == [{"role": "user", "content": "两次，半小时。"}]
    assert memory_evidence_state({"query": "q", "evidence": []})["evidence"] == []
    with pytest.raises(ValueError, match="source"):
        memory_evidence_state({"query": "q", "evidence": [{"content": "fact"}]})
    with pytest.raises(ValueError, match="eight"):
        memory_evidence_state({"query": "q", "evidence": [{"role": "user", "content": "fact"}] * 9})
    with pytest.raises(ValueError, match="independent"):
        prepare_row(
            {**row, "expected": {"evidence_sufficient": "yes", "temporal_need": "no"}},
            "memory",
            contract=MEMORY_EVIDENCE_CONTRACT,
        )
    assert profile_contract("memory", MEMORY_PAIR_CONTRACT)[0] == MEMORY_PAIR_CONTRACT


def test_sufficiency_policy_families_do_not_share_states_or_groups():
    from training.refine_pair_round import SPLITS, identity

    seen, groups = set(), set()
    for split in SPLITS:
        rows = fixtures(split)
        states = {identity(r["state"]) for r in rows}
        sg = {r["splitGroup"] for r in rows}
        assert not states & seen and not sg & groups
        seen.update(states)
        groups.update(sg)
        assert {"yes", "no"} == {r["expected"]["evidence_sufficient"] for r in rows}
        for r in rows:
            ev = r["state"]["evidence"]
            if not ev or all(c["role"] == "assistant" for c in ev):
                assert r["expected"]["evidence_sufficient"] == "no"
        assert any(
            len(r["state"]["evidence"]) == 2 and r["expected"]["evidence_sufficient"] == "yes"
            for r in rows
        )


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
