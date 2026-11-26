"""ToolACE tool prefilter: decide which tools the main model sees on each step.

The fine-tuned Qwen3-Reranker scores every candidate tool and the "reply now"
action (TEXT) against the conversation so far. Tools that score above TEXT are
offered to the main model; the rest are hidden for this step. This keeps the
main model's tool list short and lets a small local model act as a router.

Safety valves:
- `required` tools (decided by the host, e.g. support resources) are always offered.
- If the scorer is disabled, unreachable or returns misaligned scores, every
  available tool is offered and the trace records the fallback.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field

import httpx

from .config import Settings
from .public_tool_contract import CONTRACT, TEXT
from .tools import Tool


@dataclass
class PrefilterResult:
    offered: list[Tool]
    provider: str  # public_qwen | disabled | fallback
    scores: dict[str, float] = field(default_factory=dict)
    reason: str = ""

    def trace(self, step: int) -> dict:
        return {"step": step, "action": ",".join(t.name for t in self.offered) or "assistant_text",
                "provider": self.provider, "fallback": self.reason,
                "scores": {k: round(v, 3) for k, v in self.scores.items()}}


def candidate_row(history: list[dict], request: str, observations: list[dict],
                  tools: list[Tool]) -> dict:
    """Render the same row format the adapter was trained on (see training/public_tools)."""
    context = [{"role": m["role"], "content": str(m["content"])[:600]}
               for m in history[-6:] if m.get("role") in {"user", "assistant"}]
    if context and context[-1] == {"role": "user", "content": request}:
        context.pop()
    context.append({"role": "user", "content": request})
    for item in observations:
        context.append({"role": "tool_call", "content": json.dumps(
            {"name": item["name"], "arguments": item["arguments"]}, ensure_ascii=False)})
        context.append({"role": "observation", "content": json.dumps(
            item["result"], ensure_ascii=False)[:1000]})
    candidates = [{"kind": "tool", "tool": {"name": t.name, "description": t.description,
                                            "parameters": t.parameters()}} for t in tools]
    return {"context": context, "candidateTools": [*candidates, TEXT]}


class ToolPrefilter:
    def __init__(self, settings: Settings):
        self.enabled = settings.executor_provider == "public_qwen"
        self.expected_sha = settings.executor_adapter_sha256
        self.client = httpx.AsyncClient(
            base_url=settings.executor_base_url.rstrip("/"), trust_env=False,
            timeout=settings.executor_timeout_seconds,
            headers={"Authorization": "Bearer " + settings.executor_api_key},
        ) if self.enabled else None

    async def close(self):
        if self.client:
            await self.client.aclose()

    async def scores(self, row: dict, names: list[str]) -> dict[str, float]:
        response = await self.client.post("/v1/tool-scores", json={"contractVersion": CONTRACT, "row": row})
        response.raise_for_status()
        payload = response.json()
        labels = [*names, "assistant_text"]
        values = payload.get("scores")
        if (payload.get("contractVersion") != CONTRACT or payload.get("candidateIds") != labels
                or not isinstance(values, list) or len(values) != len(labels)
                or any(type(v) not in {int, float} or not math.isfinite(v) for v in values)
                or self.expected_sha and payload.get("adapterSha256") != self.expected_sha):
            raise ValueError("executor score alignment or artifact mismatch")
        return dict(zip(labels, map(float, values), strict=True))

    async def select(self, tools: list[Tool], *, history: list[dict], request: str,
                     observations: list[dict], required: set[str]) -> PrefilterResult:
        if not tools:
            return PrefilterResult([], "disabled" if not self.enabled else "public_qwen")
        if not self.enabled:
            return PrefilterResult(list(tools), "disabled")
        names = [t.name for t in tools]
        try:
            scores = await self.scores(candidate_row(history, request, observations, tools), names)
        except (httpx.HTTPError, ValueError, KeyError, TypeError):
            return PrefilterResult(list(tools), "fallback", reason="executor_unavailable_or_invalid")
        text_score = scores["assistant_text"]
        offered = [t for t in tools if scores[t.name] > text_score or t.name in required]
        return PrefilterResult(offered, "public_qwen", scores)
