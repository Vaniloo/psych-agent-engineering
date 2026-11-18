"""Dynamic read candidates; TEXT delegates to the response composer, never goal completion."""

import json
import math

import httpx

from .capabilities import CAPABILITIES, tool_description
from .public_tool_contract import CONTRACT, TEXT

BOUND = {"username", "session_id", "request_id", "model_approval"}


def descriptions():
    return {name: tool_description(name) for name in CAPABILITIES}


def candidate_row(state):
    names = state["allowedTools"]
    if not names or len(names) != len(set(names)) or len(names) > 16:
        raise ValueError("invalid available tool catalog")
    tools = []
    for name in names:
        capability = CAPABILITIES[name]
        if capability.scope.split(":")[1:2] != ["read"]:
            raise ValueError("public executor only selects registered reads")
        schema = capability.input_schema()
        bound = BOUND | {k for k, source in capability.bindings if source in BOUND}
        for field in bound:
            schema.get("properties", {}).pop(field, None)
        schema["required"] = [f for f in schema.get("required", []) if f not in bound]
        tools.append({"kind": "tool", "tool": {
            "name": name, "description": tool_description(name), "parameters": schema,
        }})
    history = [{"role": m["role"], "content": str(m["content"])[:600]}
               for m in state.get("history", [])[-6:] if m.get("role") in {"user", "assistant"}]
    if history and history[-1] == {"role": "user", "content": state["request"]}:
        history.pop()
    context = [*history, {"role": "user", "content": state["request"]}]
    for name, result in state.get("observations", {}).items():
        context.extend([
            {"role": "tool_call", "content": json.dumps({"name": name, "arguments": {}})},
            {"role": "observation", "content": result if isinstance(result, str)
             else json.dumps(result, ensure_ascii=False)},
        ])
    return {"context": context, "candidateTools": [*tools, TEXT]}


class PublicToolDecisions:
    def __init__(self, settings):
        self.expected_sha = settings.executor_adapter_sha256
        self.client = httpx.AsyncClient(
            base_url=settings.executor_base_url.rstrip("/"), trust_env=False,
            timeout=settings.executor_timeout_seconds,
            headers={"Authorization": "Bearer " + settings.executor_api_key},
        )

    async def close(self):
        await self.client.aclose()

    async def evaluate(self, state, tasks, *, labels_by_task=None):
        if tasks != ["next_action"]:
            raise ValueError("public executor cannot judge goal writes or completion")
        row = candidate_row(state)
        response = await self.client.post("/v1/tool-scores", json={"contractVersion": CONTRACT, "row": row})
        response.raise_for_status()
        payload = response.json()
        labels = [*state["allowedTools"], "assistant_text"]
        values = payload["scores"]
        if (payload.get("contractVersion") != CONTRACT or payload.get("candidateIds") != labels
                or len(values) != len(labels) or any(type(v) not in {int, float}
                or not math.isfinite(v) for v in values)
                or self.expected_sha and payload.get("adapterSha256") != self.expected_sha):
            raise ValueError("public executor artifact or score alignment mismatch")
        choice = labels[max(range(len(values)), key=values.__getitem__)]
        # This is an explicitly uncalibrated bounded ranking policy, not a
        # confidence threshold or a probability of absolute tool usefulness.
        return {"provider": "public_qwen", "contractVersion": CONTRACT,
                "model": payload["model"], "decisions": {"next_action": {
                    "choice": "finish" if choice == "assistant_text" else choice,
                    "accepted": True, "calibrated": False,
                    "policy": "constrained_argmax_with_TEXT",
                }}}
