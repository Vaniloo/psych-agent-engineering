"""Local decision boundary; no GPU packages in the Agent process."""

from __future__ import annotations

import math
from typing import Any

import httpx

from .config import Settings
from .decision_contract import ROUTER_CONTRACT, ROUTER_QUESTIONS
from .routing_contract import routing_state, validate_probabilities


class LocalDecisions:
    def __init__(
        self, url: str, key: str, timeout: float, contract: str, questions: dict, minimum: float
    ):
        if not math.isfinite(minimum) or not 0 <= minimum <= 1:
            raise ValueError("invalid minimum decision probability")
        self.contract, self.questions, self.minimum = contract, questions, minimum
        self.batch_supported = None
        self.client = httpx.AsyncClient(
            base_url=url.rstrip("/"),
            timeout=timeout,
            headers={"Authorization": f"Bearer {key}"} if key else {},
            trust_env=False,
        )

    async def close(self):
        await self.client.aclose()

    async def evaluate(
        self, state: dict, tasks: list[str], *, labels_by_task: dict | None = None
    ) -> dict:
        if not tasks or any(task not in self.questions for task in tasks):
            raise ValueError("unsupported local decision task")
        response = await self.client.post(
            "/v1/decisions", json={"contractVersion": self.contract, "state": state, "tasks": tasks}
        )
        response.raise_for_status()
        payload = response.json()
        if payload.get("contractVersion") != self.contract:
            raise ValueError("decision contract mismatch")
        result = {}
        for task in tasks:
            result[task] = self._decision(payload["answers"][task], task, labels_by_task)
        return {
            "provider": payload.get("provider", "laya"),
            "model": str(payload.get("model", "unknown")),
            "contractVersion": self.contract,
            "decisions": result,
        }

    def _decision(self, item, task, labels_by_task=None):
        labels = (labels_by_task or {}).get(task, self.questions[task]["criteria"])
        choice = item["choice"]
        probabilities = validate_probabilities(choice, item["probabilities"], labels)
        threshold = float(item["threshold"])
        if not math.isfinite(threshold) or not 0 <= threshold <= 1:
            raise ValueError("invalid task threshold")
        accepted = (
            item.get("accepted") is True
            and item.get("calibrated") is True
            and probabilities[choice] >= max(threshold, self.minimum)
        )
        return {
            "choice": choice,
            "probabilities": probabilities,
            "probability": probabilities[choice],
            "threshold": threshold,
            "accepted": accepted,
            "status": "accepted" if accepted else "abstained",
            "scope": str(item.get("validationScope", "unknown")),
            "abstentionReason": item.get("abstentionReason"),
        }

    async def evaluate_many(self, states: list[dict], task: str) -> list[dict]:
        """Bounded Qwen worker extension; each input retains its own decision."""
        if task not in self.questions or not 1 <= len(states) <= 16:
            raise ValueError("unsupported or oversized decision batch")
        if self.batch_supported is False:
            return [(await self.evaluate(state, [task]))["decisions"][task] for state in states]
        response = await self.client.post(
            "/v1/decisions/batch",
            json={"contractVersion": self.contract, "task": task, "states": states},
        )
        if response.status_code in {404, 405}:
            # Rolling back to an older Qwen worker retains the same validated decisions.
            # Auth, timeouts and malformed responses are not compatibility downgrades.
            self.batch_supported = False
            return [(await self.evaluate(state, [task]))["decisions"][task] for state in states]
        response.raise_for_status()
        payload = response.json()
        if payload.get("contractVersion") != self.contract or payload.get("task") != task:
            raise ValueError("batch decision contract mismatch")
        answers = payload["answers"]
        if len(answers) != len(states) or any(
            type(item.get("index")) is not int or item["index"] != i
            for i, item in enumerate(answers)
        ):
            raise ValueError("batch decision source alignment mismatch")
        result = [self._decision(item, task) for item in answers]
        self.batch_supported = True
        return result


class DecisionRouter(LocalDecisions):
    def __init__(self, settings: Settings):
        super().__init__(
            settings.router_base_url,
            settings.router_api_key,
            settings.router_timeout_seconds,
            ROUTER_CONTRACT,
            ROUTER_QUESTIONS,
            settings.router_min_probability,
        )

    async def decide(self, message: str, history: list[dict[str, Any]], summary: str) -> dict:
        result = await self.evaluate(
            routing_state(message, history, summary), list(ROUTER_QUESTIONS)
        )
        decisions = result["decisions"]
        route, kind = decisions["route"], decisions["conversation_kind"]
        risk, support = decisions["risk_band"], decisions["support_need"]
        return {
            **result,
            "route": route["choice"],
            "probability": route["probability"],
            "probabilities": route["probabilities"],
            "conversationKind": kind["choice"],
            "accepted": route["accepted"] and kind["accepted"],
            "kindAccepted": kind["accepted"],
            "riskDecision": {
                "provider": result["provider"],
                "riskBand": risk["choice"],
                **risk,
                "accepted": risk["accepted"]
                and (support["accepted"] or risk["choice"] == "urgent"),
                "needsProfessionalSupport": support["choice"] == "professional",
                "supportAccepted": support["accepted"],
            },
        }
