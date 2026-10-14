"""Isolated parameter copies replace only explicitly owned task answers.

Always predict the original question batch, so an unrelated task's answer stays
on the original inference path. Worker serialization protects parameter swaps.
These are full parameter copies, not LoRA or independent decision heads.
"""

import hashlib
import threading


class TaskParameterAdapter:
    def __init__(self, agent, groups, base_predict=None):
        original = agent.model.state_dict()
        owned = set()
        self.groups = []
        for tasks, parameters in groups:
            if not tasks or owned.intersection(tasks) or len(set(tasks)) != len(tasks):
                raise ValueError("overlapping or empty adapter tasks")
            if set(parameters) != set(original) or any(
                parameters[k].shape != original[k].shape for k in original
            ):
                raise ValueError("task adapter parameters mismatch")
            owned.update(tasks)
            self.groups.append(
                (
                    set(tasks),
                    {
                        k: v.to(device=original[k].device, dtype=original[k].dtype)
                        for k, v in parameters.items()
                    },
                )
            )
        self.original = {k: v.detach().clone() for k, v in original.items()}
        self.agent = agent
        self.base_predict = base_predict or agent.predict
        self.lock = threading.RLock()

    def predict(self, state, questions):
        with self.lock:
            selected = [(tasks & questions.keys(), params) for tasks, params in self.groups]
            active = [(tasks, params) for tasks, params in selected if tasks]
            if not active:
                return self.base_predict(state, questions)
            # Preserve both unowned answers and their original batch context.
            result = self.base_predict(state, questions)
            answers = dict(result["answers"])
            try:
                for tasks, parameters in active:
                    self.agent.model.load_state_dict(parameters, strict=True)
                    adapted = self.agent.predict(state, questions)
                    answers.update({task: adapted["answers"][task] for task in tasks})
                    self.agent.model.load_state_dict(self.original, strict=True)
            finally:
                self.agent.model.load_state_dict(self.original, strict=True)
            return {**result, "answers": answers}


def load_task_adapters(agent, checkpoint, metadata, questions, base_predict=None):
    from safetensors.torch import load_file

    if metadata.get("isolatedBaseWeightsSha256") != metadata.get("weightsSha256"):
        raise ValueError("isolated adapter base provenance mismatch")
    groups = []
    legacy_tasks = (
        {"relevance", "evidence_sufficient"} if metadata.get("pairAdapterSha256") else set()
    )
    for entry in metadata["isolatedTaskAdapters"]:
        tasks = entry["tasks"]
        filename = entry["file"]
        if (
            not isinstance(tasks, list)
            or any(t not in questions or t in legacy_tasks for t in tasks)
            or not isinstance(filename, str)
            or checkpoint.joinpath(filename).name != filename
            or not filename.endswith(".safetensors")
        ):
            raise ValueError("invalid isolated task adapter contract")
        path = checkpoint / filename
        if not path.is_file() or path.resolve().parent != checkpoint.resolve():
            raise ValueError("adapter outside checkpoint")
        with path.open("rb") as stream:
            if hashlib.file_digest(stream, "sha256").hexdigest() != entry["sha256"]:
                raise ValueError("isolated adapter checksum mismatch")
        groups.append((tasks, load_file(str(path))))
    return TaskParameterAdapter(agent, groups, base_predict)
