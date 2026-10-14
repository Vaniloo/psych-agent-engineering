import hashlib
import json
import sys
from types import SimpleNamespace

import pytest

from psych_agent_runtime.task_adapter import TaskParameterAdapter, load_task_adapters
from tests.test_memory_adapter import Model, Tensor


def agent():
    model = Model()

    def predict(state, questions):
        value = tuple(t.value for t in model.values.values())
        if state.get("fail") and value != (1, 2):
            raise RuntimeError("adapted prediction failed")
        return {"answers": {task: (value, tuple(questions)) for task in questions}}

    return SimpleNamespace(model=model, predict=predict)


def test_mixed_batch_keeps_unowned_answers_and_full_batch_context():
    base = agent()
    adapter = TaskParameterAdapter(
        base, [(["risk"], {"encoder.weight": Tensor(5), "head.weight": Tensor(6)})]
    )
    questions = {"route": {}, "risk": {}, "support": {}}
    original = base.predict({}, questions)
    result = adapter.predict({}, questions)
    assert result["answers"]["route"] == original["answers"]["route"]
    assert result["answers"]["support"] == original["answers"]["support"]
    assert result["answers"]["risk"] == ((5, 6), tuple(questions))
    assert base.predict({}, questions) == original
    with pytest.raises(RuntimeError):
        adapter.predict({"fail": True}, questions)
    assert base.predict({}, questions) == original


def test_rejects_partial_state_and_overlapping_ownership():
    base = agent()
    params = {"encoder.weight": Tensor(5), "head.weight": Tensor(6)}
    with pytest.raises(ValueError, match="parameters"):
        TaskParameterAdapter(base, [(["risk"], {"head.weight": Tensor(6)})])
    with pytest.raises(ValueError, match="overlapping"):
        TaskParameterAdapter(base, [(["risk"], params), (["risk"], params)])


def test_loader_checks_base_checksum_path_and_weight_integrity(tmp_path, monkeypatch):
    base = agent()
    params = {"encoder.weight": Tensor(5), "head.weight": Tensor(6)}
    monkeypatch.setitem(
        sys.modules, "safetensors.torch", SimpleNamespace(load_file=lambda _: params)
    )
    (tmp_path / "adapter.safetensors").write_bytes(b"adapter")
    metadata = {
        "weightsSha256": "base",
        "isolatedBaseWeightsSha256": "base",
        "isolatedTaskAdapters": [
            {
                "tasks": ["risk"],
                "file": "adapter.safetensors",
                "sha256": hashlib.sha256(b"adapter").hexdigest(),
            }
        ],
    }
    assert load_task_adapters(base, tmp_path, metadata, {"risk": {}}).predict({}, {"risk": {}})[
        "answers"
    ]["risk"][0] == (5, 6)
    (tmp_path / "adapter.safetensors").write_bytes(b"tampered")
    with pytest.raises(ValueError, match="checksum"):
        load_task_adapters(base, tmp_path, metadata, {"risk": {}})
    metadata["isolatedTaskAdapters"][0]["file"] = "../adapter.safetensors"
    with pytest.raises(ValueError, match="contract"):
        load_task_adapters(base, tmp_path, metadata, {"risk": {}})
    metadata["isolatedBaseWeightsSha256"] = "other-base"
    with pytest.raises(ValueError, match="provenance"):
        load_task_adapters(base, tmp_path, metadata, {"risk": {}})


def test_worker_uses_adapter_for_target_task_and_reports_artifact(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    import psych_agent_runtime.task_adapter as module
    from psych_agent_runtime.decision_contract import ROUTER_CONTRACT, ROUTER_QUESTIONS
    from psych_agent_runtime.routing_service import create_app, questions_digest

    model = Model()

    def predict(state, questions):
        answers = {}
        for task, q in questions.items():
            choice = (
                "urgent"
                if task == "risk_band" and model.values["head.weight"].value == 6
                else next(iter(q["criteria"]))
            )
            others = len(q["criteria"]) - 1
            answers[task] = {
                "choice": choice,
                "probabilities": {k: 0.99 if k == choice else 0.01 / others for k in q["criteria"]},
            }
        return {"answers": answers}

    base = SimpleNamespace(
        model=model,
        predict=predict,
        cfg={"max_len": 512, "head_max_len": 192},
        tok=SimpleNamespace(mask_token="[MASK]", encode=lambda *_a, **_k: [1]),
    )
    params = {"encoder.weight": Tensor(5), "head.weight": Tensor(6)}
    monkeypatch.setattr(
        module,
        "load_task_adapters",
        lambda *_a, **_k: TaskParameterAdapter(base, [(["risk_band"], params)]),
    )
    monkeypatch.setitem(sys.modules, "laya", SimpleNamespace(load=lambda *_a, **_k: base))
    monkeypatch.setitem(sys.modules, "torch", SimpleNamespace(set_num_threads=lambda _: None))
    weights = b"base"
    (tmp_path / "model.safetensors").write_bytes(weights)
    adapters = [{"tasks": ["risk_band"], "file": "adapter.safetensors", "sha256": "test-adapter"}]
    metadata = {
        "profile": "router",
        "contractVersion": ROUTER_CONTRACT,
        "questionsSha256": questions_digest(ROUTER_QUESTIONS),
        "weightsSha256": hashlib.sha256(weights).hexdigest(),
        "amp": "fp16",
        "model": "isolated-test",
        "isolatedTaskAdapters": adapters,
        "tasks": {
            t: {"enabled": True, "calibrated": True, "temperature": 1, "threshold": 0.85}
            for t in ROUTER_QUESTIONS
        },
    }
    (tmp_path / "deployment.json").write_text(json.dumps(metadata))
    for key, value in {
        "DECISION_PROFILE": "router",
        "DECISION_CHECKPOINT": str(tmp_path),
        "DECISION_API_KEY": "test-token",
        "ROUTER_DEVICE": "cpu",
    }.items():
        monkeypatch.setenv(key, value)
    with TestClient(create_app()) as client:
        assert client.get("/health").json()["artifact"]["isolatedTaskAdapters"] == adapters
        response = client.post(
            "/v1/decisions",
            headers={"Authorization": "Bearer test-token"},
            json={
                "contractVersion": ROUTER_CONTRACT,
                "state": {"message": "test"},
                "tasks": list(ROUTER_QUESTIONS),
            },
        )
        assert response.status_code == 200
        answers = response.json()["answers"]
        assert answers["risk_band"]["choice"] == "urgent"
        assert answers["risk_band"]["accepted"]
        assert answers["route"]["choice"] == "chat"
        assert tuple(t.value for t in model.values.values()) == (1, 2)


@pytest.mark.parametrize(
    "metadata",
    [
        {"tasks": {"risk_band": {"classPolicies": {"urgent": {"enabled": False}}}}},
        {"calibrationProbabilityEncoding": "laya_logit_softmax_float64_offline_v1", "tasks": {}},
    ],
)
def test_experimental_category_policy_cannot_be_silently_ignored_by_serving(
    tmp_path, monkeypatch, metadata
):
    from fastapi.testclient import TestClient

    from psych_agent_runtime.routing_service import create_app

    (tmp_path / "deployment.json").write_text(json.dumps(metadata))
    monkeypatch.setenv("DECISION_CHECKPOINT", str(tmp_path))
    monkeypatch.setenv("DECISION_PROFILE", "router")
    monkeypatch.setitem(sys.modules, "laya", SimpleNamespace())
    monkeypatch.setitem(sys.modules, "torch", SimpleNamespace())
    with pytest.raises(ValueError, match="reviewed serving support"), TestClient(create_app()):
        pass
