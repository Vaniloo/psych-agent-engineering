"""Task-specific Memory parameters restore the unchanged base after each score."""

import hashlib
import threading


class MemoryHeadAdapter:
    def __init__(self, agent, state, *, full=False, task_states=None):
        original = agent.model.state_dict()
        expected = set(original) if full else {k for k in original if not k.startswith("encoder.")}
        if set(state) != expected or any(state[k].shape != original[k].shape for k in state):
            raise ValueError("memory head adapter parameters mismatch")
        self.agent = agent
        task_states = task_states or {}
        for task, parameters in task_states.items():
            if (
                task != "evidence_sufficient"
                or set(parameters) != set(original)
                or any(parameters[k].shape != original[k].shape for k in parameters)
            ):
                raise ValueError("memory task adapter parameters mismatch")
        restore_keys = set(original) if task_states else expected
        self.original = {k: original[k].detach().clone() for k in restore_keys}
        self.adapted = {
            k: v.to(device=original[k].device, dtype=original[k].dtype) for k, v in state.items()
        }
        self.lock = threading.RLock()
        self.adapters = {
            "relevance": self.adapted,
            **{
                task: {
                    k: v.to(device=original[k].device, dtype=original[k].dtype)
                    for k, v in parameters.items()
                }
                for task, parameters in task_states.items()
            },
        }

    def predict(self, state, questions):
        with self.lock:
            parameters = self.adapters.get(next(iter(questions))) if len(questions) == 1 else None
            if parameters is None:
                return self.agent.predict(state, questions)
            try:
                self.agent.model.load_state_dict(parameters, strict=False)
                return self.agent.predict(state, questions)
            finally:
                self.agent.model.load_state_dict(self.original, strict=False)


def load_memory_head(agent, checkpoint, metadata):
    from safetensors.torch import load_file

    if metadata.get("baseWeightsSha256") != metadata.get("weightsSha256"):
        raise ValueError("memory base provenance mismatch")
    full = bool(metadata.get("pairAdapterSha256"))
    path = checkpoint / ("pair-adapter.safetensors" if full else "pair-head.safetensors")
    with path.open("rb") as stream:
        checksum = hashlib.file_digest(stream, "sha256").hexdigest()
    if checksum != metadata.get("pairAdapterSha256" if full else "pairHeadSha256"):
        raise ValueError("memory head checksum mismatch")
    task_states = {}
    for task, entry in metadata.get("taskAdapters", {}).items():
        if task != "evidence_sufficient" or not isinstance(entry, dict):
            raise ValueError("unsupported memory task adapter")
        filename = entry.get("file")
        if not isinstance(filename, str) or checkpoint.joinpath(filename).name != filename:
            raise ValueError("invalid memory task adapter path")
        task_path = checkpoint / filename
        with task_path.open("rb") as stream:
            if hashlib.file_digest(stream, "sha256").hexdigest() != entry.get("sha256"):
                raise ValueError("memory task adapter checksum mismatch")
        task_states[task] = load_file(str(task_path))
    return MemoryHeadAdapter(agent, load_file(str(path)), full=full, task_states=task_states)
