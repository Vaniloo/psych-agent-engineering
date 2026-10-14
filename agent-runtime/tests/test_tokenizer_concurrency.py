import asyncio
import hashlib
import json
import sys
from threading import Event
from types import SimpleNamespace

import httpx
import pytest

from psych_agent_runtime.decision_contract import EXECUTOR_CONTRACT, EXECUTOR_QUESTIONS
from psych_agent_runtime.routing_service import create_app, questions_digest


@pytest.mark.asyncio
async def test_token_budget_check_cannot_borrow_tokenizer_during_prediction(tmp_path, monkeypatch):
    weights = b"concurrent worker fixture"
    (tmp_path / "model.safetensors").write_bytes(weights)
    (tmp_path / "deployment.json").write_text(
        json.dumps(
            {
                "profile": "executor",
                "contractVersion": EXECUTOR_CONTRACT,
                "questionsSha256": questions_digest(EXECUTOR_QUESTIONS),
                "amp": "fp16",
                "weightsSha256": hashlib.sha256(weights).hexdigest(),
                "maxStateTokens": 256,
                "tasks": {
                    "next_action": {
                        "enabled": True,
                        "calibrated": True,
                        "temperature": 1,
                        "threshold": 0.85,
                    }
                },
            }
        )
    )
    borrowed, release = Event(), Event()

    def encode(text, **kwargs):
        if borrowed.is_set():
            raise RuntimeError("Already borrowed")
        return list(text)

    def predict(*args):
        borrowed.set()
        try:
            assert release.wait(3)
            return {
                "answers": {
                    "next_action": {
                        "choice": "get_dashboard",
                        "probabilities": {"get_dashboard": 0.9, "finish": 0.05, "clarify": 0.05},
                    }
                }
            }
        finally:
            borrowed.clear()

    agent = SimpleNamespace(
        cfg={"max_len": 512, "head_max_len": 192},
        tok=SimpleNamespace(mask_token="[MASK]", encode=encode),
        predict=predict,
    )
    monkeypatch.setitem(sys.modules, "laya", SimpleNamespace(load=lambda *a, **k: agent))
    monkeypatch.setitem(sys.modules, "torch", SimpleNamespace(set_num_threads=lambda _: None))
    for k, v in {
        "DECISION_PROFILE": "executor",
        "DECISION_CHECKPOINT": str(tmp_path),
        "DECISION_API_KEY": "test-secret",
        "ROUTER_DEVICE": "cpu",
    }.items():
        monkeypatch.setenv(k, v)
    app = create_app()
    payload = {
        "contractVersion": EXECUTOR_CONTRACT,
        "state": {"allowedTools": ["get_dashboard"]},
        "tasks": ["next_action"],
    }
    async with app.router.lifespan_context(app), httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
        headers={"Authorization": "Bearer test-secret"},
    ) as client:
        first = asyncio.create_task(client.post("/v1/decisions", json=payload))
        try:
            assert await asyncio.to_thread(borrowed.wait, 1)
            second = await client.post("/v1/decisions", json=payload)
            assert second.status_code == 503  # bounded busy response, never a tokenizer crash
        finally:
            release.set()
            result = await first
        assert result.status_code == 200
        assert (await client.post("/v1/decisions", json=payload)).status_code == 200
