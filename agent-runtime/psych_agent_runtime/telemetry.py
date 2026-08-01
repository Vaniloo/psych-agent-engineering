from __future__ import annotations

import time
from contextvars import ContextVar, Token
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from .config import Settings


@dataclass
class RunTelemetry:
    request_id: str
    settings: Settings
    started_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    started_perf: float = field(default_factory=time.perf_counter)
    node_durations_ms: dict[str, float] = field(default_factory=dict)
    model_calls: list[dict[str, Any]] = field(default_factory=list)
    retrieval: dict[str, Any] = field(default_factory=dict)
    mcp_duration_ms: float = 0.0
    fallback_count: int = 0
    ttft_ms: float | None = None
    finalized: bool = False

    def mark_first_token(self) -> None:
        if self.ttft_ms is None:
            self.ttft_ms = self._elapsed_ms()

    def record_node(self, node: str, duration_ms: float) -> None:
        self.node_durations_ms[node] = round(
            self.node_durations_ms.get(node, 0.0) + duration_ms, 2
        )

    def record_model_call(
        self,
        *,
        purpose: str,
        model: str,
        payload: dict[str, Any],
    ) -> None:
        prompt_tokens = int(payload.get("prompt_eval_count") or 0)
        completion_tokens = int(payload.get("eval_count") or 0)
        duration_ms = self._nanoseconds_to_ms(payload.get("total_duration"))
        load_duration_ms = self._nanoseconds_to_ms(payload.get("load_duration"))
        eval_duration_ms = self._nanoseconds_to_ms(payload.get("eval_duration"))
        tokens_per_second = (
            completion_tokens / (eval_duration_ms / 1000)
            if completion_tokens and eval_duration_ms
            else 0.0
        )
        self.model_calls.append(
            {
                "purpose": purpose,
                "model": model,
                "promptTokens": prompt_tokens,
                "completionTokens": completion_tokens,
                "totalTokens": prompt_tokens + completion_tokens,
                "durationMs": round(duration_ms, 2),
                "loadDurationMs": round(load_duration_ms, 2),
                "evalDurationMs": round(eval_duration_ms, 2),
                "tokensPerSecond": round(tokens_per_second, 2),
            }
        )

    def record_retrieval(self, metrics: dict[str, Any]) -> None:
        self.retrieval = dict(metrics)

    def record_mcp(self, duration_ms: float, fallback: bool = False) -> None:
        self.mcp_duration_ms += duration_ms
        if fallback:
            self.fallback_count += 1

    def snapshot(
        self,
        *,
        status: str = "success",
        persistence_status: str = "saved",
    ) -> dict[str, Any]:
        prompt_tokens = sum(int(item["promptTokens"]) for item in self.model_calls)
        completion_tokens = sum(
            int(item["completionTokens"]) for item in self.model_calls
        )
        eval_seconds = sum(float(item.get("evalDurationMs") or 0) for item in self.model_calls) / 1000
        input_cost = (
            prompt_tokens * self.settings.input_cost_per_million_tokens / 1_000_000
        )
        output_cost = (
            completion_tokens
            * self.settings.output_cost_per_million_tokens
            / 1_000_000
        )
        estimated_cost = input_cost + output_cost
        actual_cost = estimated_cost if self.settings.cost_mode != "local" else 0.0
        total_tokens = prompt_tokens + completion_tokens
        return {
            "requestId": self.request_id,
            "model": self.settings.chat_model,
            "startedAt": self.started_at.isoformat(),
            "status": status,
            "totalDurationMs": round(self._elapsed_ms(), 2),
            "ttftMs": None if self.ttft_ms is None else round(self.ttft_ms, 2),
            "promptTokens": prompt_tokens,
            "completionTokens": completion_tokens,
            "totalTokens": total_tokens,
            "modelCallCount": len(self.model_calls),
            "modelCalls": list(self.model_calls),
            "tokensPerSecond": round(
                completion_tokens / eval_seconds if eval_seconds else 0.0, 2
            ),
            "actualCostUsd": round(actual_cost, 8),
            "estimatedCostUsd": round(estimated_cost, 8),
            "costMode": self.settings.cost_mode,
            "nodeDurationsMs": dict(self.node_durations_ms),
            "retrieval": dict(self.retrieval),
            "mcpDurationMs": round(self.mcp_duration_ms, 2),
            "fallbackCount": self.fallback_count,
            "persistenceStatus": persistence_status,
        }

    def _elapsed_ms(self) -> float:
        return (time.perf_counter() - self.started_perf) * 1000

    @staticmethod
    def _nanoseconds_to_ms(value: Any) -> float:
        try:
            return float(value or 0) / 1_000_000
        except (TypeError, ValueError):
            return 0.0


run_telemetry_context: ContextVar[RunTelemetry | None] = ContextVar(
    "run_telemetry", default=None
)


def start_run(settings: Settings, request_id: str) -> tuple[RunTelemetry, Token]:
    telemetry = RunTelemetry(request_id=request_id, settings=settings)
    return telemetry, run_telemetry_context.set(telemetry)


def current_run() -> RunTelemetry | None:
    return run_telemetry_context.get()


def end_run(token: Token) -> None:
    run_telemetry_context.reset(token)
