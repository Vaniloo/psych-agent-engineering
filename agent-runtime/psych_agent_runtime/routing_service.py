"""One isolated Laya process per router, executor, or memory profile.

No checkpoint is a supported state: health advertises unavailable tasks and the
Agent uses its bounded fallback. A flat v1 router is never a v2 risk model.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import math
import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, Header, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from .decision_contract import (
    MEMORY_EVIDENCE_CONTRACT,
    MEMORY_PAIR_CONTRACTS,
    PROFILES,
    TOOL_CHOICES,
    focused_memory_questions,
    memory_evidence_state,
    memory_pair_state,
    profile_contract,
)
from .routing_contract import validate_probabilities


def questions_digest(questions: dict) -> str:
    return hashlib.sha256(
        json.dumps(questions, ensure_ascii=False, sort_keys=True).encode()
    ).hexdigest()


def calibrate_probabilities(probs: dict[str, float], policy: dict) -> dict[str, float]:
    """Apply the temperature fitted on held-out data, before thresholding."""
    if policy.get("calibrated") is not True:
        return probs
    temperature = float(policy["temperature"])
    if not math.isfinite(temperature) or temperature <= 0:
        raise ValueError("invalid checkpoint calibration temperature")
    logits = {name: math.log(max(prob, 1e-30)) / temperature for name, prob in probs.items()}
    highest = max(logits.values())
    scores = {name: math.exp(value - highest) for name, value in logits.items()}
    total = sum(scores.values())
    return {name: score / total for name, score in scores.items()}


class DecisionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    contractVersion: str
    state: dict
    tasks: list[str] = Field(min_length=1, max_length=14)


def create_app() -> FastAPI:
    profile = os.getenv("DECISION_PROFILE", "router")
    if profile not in PROFILES:
        raise ValueError("invalid decision profile")
    contract, questions = profile_contract(profile, os.getenv("DECISION_CONTRACT") or None)
    agent, metadata, adapter = None, {}, None
    lock = asyncio.Lock()
    key = os.getenv("DECISION_API_KEY", os.getenv("ROUTER_API_KEY", ""))

    @asynccontextmanager
    async def lifespan(_app):
        nonlocal agent, metadata, contract, questions, adapter
        if os.getenv("ROUTER_HOST", "127.0.0.1") not in {"127.0.0.1", "localhost"} and not key:
            raise ValueError("non-loopback decision service requires an API key")
        checkpoint = os.getenv("DECISION_CHECKPOINT", "")
        if checkpoint:
            import laya
            import torch

            path = Path(checkpoint)
            metadata = json.loads((path / "deployment.json").read_text())
            configured_contract = os.getenv("DECISION_CONTRACT")
            if configured_contract and configured_contract != metadata.get("contractVersion"):
                raise ValueError("configured contract differs from checkpoint")
            contract, questions = profile_contract(profile, metadata.get("contractVersion"))
            if metadata.get("promotionStatus") == "rejected":
                raise ValueError("checkpoint rejected by deployment evaluation")
            if (
                metadata.get("contractVersion") != contract
                or metadata.get("profile") != profile
                or metadata.get("questionsSha256") != questions_digest(questions)
            ):
                raise ValueError("checkpoint task/profile contract mismatch")
            with (path / "model.safetensors").open("rb") as stream:
                digest = hashlib.file_digest(stream, "sha256").hexdigest()
            if digest != metadata.get("weightsSha256"):
                raise ValueError("checkpoint checksum mismatch")
            if metadata.get("amp") != "fp16":
                raise ValueError("checkpoint precision mismatch")
            os.environ["LAYA_CUDA_AMP"] = "fp16"
            torch.set_num_threads(4)
            device = os.getenv("ROUTER_DEVICE", "cpu")
            agent = laya.load(str(path), device=device)
            if (
                metadata.get("pairHeadSha256")
                or metadata.get("pairAdapterSha256")
                or metadata.get("taskAdapters")
            ):
                if profile != "memory" or contract not in MEMORY_PAIR_CONTRACTS:
                    raise ValueError("pair head requires the versioned memory profile")
                from .memory_head import load_memory_head

                adapter = load_memory_head(agent, path, metadata)
            if device.startswith("cuda") and not str(agent.device).startswith("cuda"):
                raise ValueError("requested GPU was not used")
            limit = metadata.get("maxStateTokens", 256)
            room = agent.cfg.get("max_len", 512) - agent.cfg.get("head_max_len", 192) - 16
            if type(limit) is not int or not 0 < limit <= room:
                raise ValueError("checkpoint state budget exceeds encoder capacity")
            for task_policy in metadata.get("tasks", {}).values():
                calibrate_probabilities({"yes": 0.5, "no": 0.5}, task_policy)
        yield
        agent = None
        adapter = None

    app = FastAPI(title=f"Local Laya {profile} decisions", lifespan=lifespan)

    def authenticate(authorization: str = Header(default="")):
        if key and not hmac.compare_digest(authorization.encode(), f"Bearer {key}".encode()):
            raise HTTPException(401, "invalid decision credentials")

    @app.get("/health")
    async def health():
        return {
            "ready": agent is not None,
            "profile": profile,
            "contractVersion": contract,
            "model": metadata.get("model"),
            "artifact": {
                "weightsSha256": metadata.get("weightsSha256"),
                "trainedAt": metadata.get("trainedAt"),
                "trainingScope": metadata.get("trainingScope"),
                "dataRows": metadata.get("dataRows"),
                "pairHeadSha256": metadata.get("pairHeadSha256"),
                "pairAdapterSha256": metadata.get("pairAdapterSha256"),
                "baseModel": metadata.get("baseModel"),
                "taskAdapters": metadata.get("taskAdapters"),
            },
            "tasks": {
                name: metadata.get("tasks", {}).get(name, {}).get("enabled", False)
                for name in questions
            },
        }

    @app.post("/v1/decisions", dependencies=[Depends(authenticate)])
    async def decide(request: DecisionRequest):
        if request.contractVersion != contract:
            raise HTTPException(409, "decision contract mismatch")
        if any(name not in questions for name in request.tasks) or len(set(request.tasks)) != len(
            request.tasks
        ):
            raise HTTPException(422, "unsupported or duplicate task")
        if len(json.dumps(request.state, ensure_ascii=False)) > 24000:
            raise HTTPException(422, "state too large")
        if agent is None:
            raise HTTPException(503, "checkpoint not configured")
        try:
            state = request.state
            if contract in MEMORY_PAIR_CONTRACTS and "relevance" in request.tasks:
                if request.tasks != ["relevance"]:
                    raise ValueError("pair scoring must be an independent decision")
                state = memory_pair_state(state)
            if contract == MEMORY_EVIDENCE_CONTRACT and "evidence_sufficient" in request.tasks:
                if request.tasks != ["evidence_sufficient"]:
                    raise ValueError("sufficiency must be an independent decision")
                state = memory_evidence_state(state)
            selected = (
                focused_memory_questions(state, request.tasks)
                if profile == "memory" and contract not in MEMORY_PAIR_CONTRACTS
                else {name: questions[name] for name in request.tasks}
            )
        except (ValueError, TypeError) as exc:
            raise HTTPException(422, "invalid memory candidate set") from exc
        if profile == "executor" and "next_action" in selected:
            allowed = request.state.get("allowedTools", [])
            if not isinstance(allowed, list) or any(
                name not in TOOL_CHOICES or name in {"finish", "clarify"} for name in allowed
            ):
                raise HTTPException(422, "invalid tool permission set")
            choices = {
                name: TOOL_CHOICES[name] for name in [*dict.fromkeys(allowed), "finish", "clarify"]
            }
            selected["next_action"] = {**questions["next_action"], "criteria": choices}
        if profile == "executor" and "goal_action" in selected:
            candidates = request.state.get("candidates", [])
            if (
                not isinstance(candidates, list)
                or not 1 <= len(candidates) <= 8
                or any(
                    not isinstance(item, dict)
                    or not isinstance(item.get("goalTitle"), str)
                    or not isinstance(item.get("actionTitle"), str)
                    for item in candidates
                )
            ):
                raise HTTPException(422, "invalid goal candidates")
            choices = {
                "none": questions["goal_action"]["criteria"]["none"],
                **{
                    f"action_{i}": f"候选{i}：目标“{item['goalTitle']}”中的小行动“{item['actionTitle']}”"
                    for i, item in enumerate(candidates)
                },
            }
            selected["goal_action"] = {**questions["goal_action"], "criteria": choices}
        # Explicitly refuse oversized serialized input rather than silently
        # accepting a low-risk result after the model discards evidence.
        limit = int(metadata.get("maxStateTokens", 256))
        tokenizer = agent.tok
        text = json.dumps(state, ensure_ascii=False).replace(tokenizer.mask_token, " ")
        if len(tokenizer.encode(text, add_special_tokens=False)) > limit:
            raise HTTPException(422, "decision state exceeds checkpoint token budget")
        try:
            await asyncio.wait_for(lock.acquire(), timeout=0.1)
        except TimeoutError as exc:
            raise HTTPException(503, "decision worker busy") from exc
        try:
            predict = adapter.predict if adapter else agent.predict
            task = asyncio.create_task(asyncio.to_thread(predict, state, selected))
            try:
                result = await asyncio.shield(task)
            except asyncio.CancelledError:
                await task  # cancellation does not stop the worker thread
                raise
            answers = {}
            for name in request.tasks:
                answer = result["answers"][name]
                choice = answer["choice"]
                probs = validate_probabilities(
                    choice, answer["probabilities"], selected[name]["criteria"]
                )
                policy = metadata.get("tasks", {}).get(name, {})
                probs = calibrate_probabilities(probs, policy)
                threshold = float(policy.get("threshold", 1))
                if not math.isfinite(threshold) or not 0 <= threshold <= 1:
                    raise ValueError("invalid checkpoint threshold")
                answers[name] = {
                    "choice": choice,
                    "probabilities": probs,
                    "calibrated": policy.get("calibrated") is True,
                    "threshold": threshold,
                    "validationScope": policy.get("validationScope", "unknown"),
                    "accepted": bool(
                        policy.get("enabled") is True
                        and policy.get("calibrated") is True
                        and probs[choice] >= threshold
                    ),
                }
            return {"contractVersion": contract, "model": metadata.get("model"), "answers": answers}
        finally:
            lock.release()

    return app


app = create_app()
