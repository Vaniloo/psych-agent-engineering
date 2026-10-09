"""Optional single-checkpoint service. Install the routing extra to run it.

Bind to loopback by default. Remote/container access requires ROUTER_API_KEY.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, Header, HTTPException
from pydantic import BaseModel, Field

from .routing_contract import CONTRACT_VERSION, QUESTIONS, routing_state, validate_probabilities


class State(BaseModel):
    message: str = Field(min_length=1, max_length=4000)
    history: list[dict[str, str]] = Field(default_factory=list, max_length=4)
    session_summary: str = Field(default="", max_length=400)


class RouteRequest(BaseModel):
    contractVersion: str
    state: State


def create_app() -> FastAPI:
    agent = None
    metadata = {}
    lock = asyncio.Lock()
    api_key = os.getenv("ROUTER_API_KEY", "")

    @asynccontextmanager
    async def lifespan(_app):
        nonlocal agent, metadata
        import laya
        import torch

        model_path = Path(os.environ["ROUTER_CHECKPOINT"])
        metadata = json.loads((model_path / "deployment.json").read_text(encoding="utf-8"))
        if metadata["contractVersion"] != CONTRACT_VERSION or metadata.get("amp") != "fp16":
            raise ValueError("checkpoint contract or precision mismatch")
        with (model_path / "model.safetensors").open("rb") as stream:
            digest = hashlib.file_digest(stream, "sha256").hexdigest()
        if digest != metadata["weightsSha256"]:
            raise ValueError("checkpoint checksum mismatch")
        if os.getenv("ROUTER_HOST", "127.0.0.1") != "127.0.0.1" and not api_key:
            raise ValueError("non-loopback serving requires ROUTER_API_KEY")
        os.environ["LAYA_CUDA_AMP"] = "fp16"
        torch.set_num_threads(4)
        device = os.getenv("ROUTER_DEVICE", "cpu")
        if device.startswith("cuda"):
            if not torch.cuda.is_available():
                raise ValueError("CUDA requested but not available")
            torch.cuda.set_per_process_memory_fraction(0.10)
        agent = laya.load(str(model_path), device=device)
        if device.startswith("cuda") and not str(agent.device).startswith("cuda"):
            raise ValueError("model silently fell back from CUDA")
        await asyncio.to_thread(agent.predict, routing_state("你好"), QUESTIONS)
        yield
        agent = None

    app = FastAPI(title="Psych Agent Decision Router", lifespan=lifespan)

    def authenticate(authorization: str = Header(default="")):
        if api_key and not hmac.compare_digest(authorization, f"Bearer {api_key}"):
            raise HTTPException(401, "invalid router credentials")

    @app.get("/health")
    async def health():
        return {
            "ready": agent is not None,
            "model": metadata.get("model"),
            "enabled": metadata.get("enabled", False),
            "scope": metadata.get("validationScope"),
        }

    @app.post("/v1/route", dependencies=[Depends(authenticate)])
    async def decide(request: RouteRequest):
        if request.contractVersion != CONTRACT_VERSION:
            raise HTTPException(409, "routing contract mismatch")
        try:
            await asyncio.wait_for(lock.acquire(), timeout=0.1)
        except TimeoutError as exc:
            raise HTTPException(503, "router busy") from exc
        try:
            prediction = asyncio.create_task(
                asyncio.to_thread(
                    agent.predict,
                    routing_state(
                        request.state.message, request.state.history, request.state.session_summary
                    ),
                    QUESTIONS,
                )
            )
            try:
                result = await asyncio.shield(prediction)
            except asyncio.CancelledError:
                # Cancellation cannot stop the worker thread. Keep the lock until
                # inference finishes so a retry never runs concurrently with it.
                await prediction
                raise
            answer = result["answers"]["route"]
            route = answer["choice"]
            probabilities = validate_probabilities(route, answer["probabilities"])
            threshold = float(metadata["threshold"])
            return {
                **metadata,
                "route": route,
                "probabilities": probabilities,
                "accepted": bool(
                    metadata.get("enabled")
                    and metadata.get("calibrated")
                    and probabilities[route] >= threshold
                ),
            }
        finally:
            lock.release()

    return app


app = create_app()
