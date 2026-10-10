"""Task-specific Memory parameters restore the unchanged base after each score."""

import hashlib
import threading


class MemoryHeadAdapter:
    def __init__(self, agent, state, *, full=False):
        original = agent.model.state_dict()
        expected = set(original) if full else {k for k in original if not k.startswith("encoder.")}
        if set(state) != expected or any(state[k].shape != original[k].shape for k in state):
            raise ValueError("memory head adapter parameters mismatch")
        self.agent = agent
        self.original = {k: original[k].detach().clone() for k in expected}
        self.adapted = {
            k: v.to(device=original[k].device, dtype=original[k].dtype) for k, v in state.items()
        }
        self.lock = threading.RLock()

    def predict(self, state, questions):
        with self.lock:
            if set(questions) != {"relevance"}:
                return self.agent.predict(state, questions)
            try:
                self.agent.model.load_state_dict(self.adapted, strict=False)
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
    return MemoryHeadAdapter(agent, load_file(str(path)), full=full)
