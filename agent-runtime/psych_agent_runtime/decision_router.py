from __future__ import annotations

import time
from typing import Any

import httpx

from .config import Settings
from .routing_contract import CONTRACT_VERSION, routing_state, validate_probabilities


class DecisionRouter:
    """HTTP boundary keeps GPU dependencies outside the business Agent process."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        if not 0 <= settings.router_min_probability <= 1:
            raise ValueError("ROUTER_MIN_PROBABILITY must be between zero and one")
        headers = {}
        if settings.router_api_key:
            headers["Authorization"] = f"Bearer {settings.router_api_key}"
        self.client = httpx.AsyncClient(
            base_url=settings.router_base_url.rstrip("/"),
            timeout=settings.router_timeout_seconds,
            headers=headers,
            trust_env=False,
        )

    async def close(self) -> None:
        await self.client.aclose()

    async def decide(
        self, message: str, history: list[dict[str, Any]], summary: str
    ) -> dict[str, Any]:
        started = time.perf_counter()
        response = await self.client.post(
            "/v1/route",
            json={
                "contractVersion": CONTRACT_VERSION,
                "state": routing_state(message, history, summary),
            },
        )
        response.raise_for_status()
        result = response.json()
        if not isinstance(result, dict):
            raise TypeError("router response must be an object")
        if result.get("contractVersion") != CONTRACT_VERSION:
            raise ValueError("routing contract version mismatch")
        route = result["route"]
        probabilities = validate_probabilities(route, result["probabilities"])
        threshold = float(result["threshold"])
        if not 0 <= threshold <= 1:
            raise ValueError("invalid routing threshold")
        accepted = (
            result.get("calibrated") is True
            and result.get("accepted") is True
            and probabilities[route] >= max(threshold, self.settings.router_min_probability)
        )
        return {
            "provider": "laya",
            "model": str(result.get("model", "unknown")),
            "route": route,
            "probabilities": probabilities,
            "probability": probabilities[route],
            "threshold": threshold,
            "accepted": accepted,
            "scope": str(result.get("validationScope", "unknown")),
            "durationMs": round((time.perf_counter() - started) * 1000, 2),
        }
