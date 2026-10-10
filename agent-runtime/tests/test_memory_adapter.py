from types import SimpleNamespace

import pytest

from psych_agent_runtime.memory_head import MemoryHeadAdapter


class Tensor:
    shape = (1,)
    device = "cpu"
    dtype = "float32"

    def __init__(self, value):
        self.value = value

    def detach(self):
        return self

    def clone(self):
        return Tensor(self.value)

    def to(self, **kwargs):
        return self.clone()


class Model:
    def __init__(self):
        self.values = {"encoder.weight": Tensor(1), "head.weight": Tensor(2)}

    def state_dict(self):
        return self.values

    def load_state_dict(self, state, **kwargs):
        for key, tensor in state.items():
            self.values[key].value = tensor.value


def test_full_adapter_is_task_scoped_and_restores_even_after_failure():
    model = Model()

    def predict(state, questions):
        if state.get("fail"):
            raise RuntimeError("prediction failed")
        return tuple(t.value for t in model.values.values())

    agent = SimpleNamespace(model=model, predict=predict)
    adapter = MemoryHeadAdapter(
        agent, {"encoder.weight": Tensor(10), "head.weight": Tensor(20)}, full=True
    )
    assert adapter.predict({}, {"semantic": {}}) == (1, 2)
    assert adapter.predict({}, {"relevance": {}}) == (10, 20)
    assert adapter.predict({}, {"semantic": {}}) == (1, 2)
    with pytest.raises(RuntimeError, match="prediction failed"):
        adapter.predict({"fail": True}, {"relevance": {}})
    assert adapter.predict({}, {"semantic": {}}) == (1, 2)


def test_head_adapter_cannot_modify_encoder_or_omit_a_parameter():
    agent = SimpleNamespace(model=Model())
    with pytest.raises(ValueError, match="parameters"):
        MemoryHeadAdapter(agent, {"encoder.weight": Tensor(5), "head.weight": Tensor(6)})
    with pytest.raises(ValueError, match="parameters"):
        MemoryHeadAdapter(agent, {"head.weight": Tensor(6)}, full=True)
