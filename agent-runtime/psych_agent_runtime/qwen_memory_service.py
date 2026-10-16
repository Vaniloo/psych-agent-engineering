"""Experimental local Qwen4B+LoRA worker; fixed Memory tasks, no business writes."""

import argparse
import asyncio
import hashlib
import hmac
import json
import math
import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel, Field

from .qwen_memory_contract import INSTRUCTIONS, normalize, render


class DecisionRequest(BaseModel):
    contractVersion: str
    state: dict
    tasks: list[str] = Field(min_length=1, max_length=12)


class BatchDecisionRequest(BaseModel):
    contractVersion: str
    task: str
    states: list[dict] = Field(min_length=1, max_length=16)


MINIMUM_RAW_MARGIN = 2.0


def calibrated_answer(policy, no_logit, yes_logit):
    """Serving-only BF16 stability heuristic, separate from learned calibration.

    A small temperature can turn a weak raw margin into a confident answer.
    Abstain on raw margins below two logits rather than treating that confidence
    as numerical stability. This heuristic is not a calibrated error guarantee.
    """
    delta = yes_logit - no_logit
    if not math.isfinite(delta):
        raise ValueError("non-finite model logits")
    z = delta / policy["temperature"]
    p = 1 / (1 + math.exp(-z)) if z >= 0 else math.exp(z) / (1 + math.exp(z))
    choice = "yes" if delta >= 0 else "no"
    selected = p if choice == "yes" else 1 - p
    stable = abs(delta) >= MINIMUM_RAW_MARGIN
    return {
        "choice": choice,
        "probabilities": {"yes": p, "no": 1 - p},
        "threshold": policy["threshold"],
        "calibrated": policy["calibrated"],
        "accepted": policy["enabled"] and stable and selected >= policy["threshold"],
        "validationScope": policy["validationScope"],
        "stabilityMargin": abs(delta),
        "stabilityMinimum": MINIMUM_RAW_MARGIN,
        "abstentionReason": None if stable else "small_raw_logit_margin",
    }


def create_app(base, checkpoint):
    base, checkpoint = Path(base), Path(checkpoint)
    report = json.loads((checkpoint / "training-report.json").read_text())
    contract = report.get("contractVersion", "psych-memory-v4")
    instructions, normalizer, renderer = INSTRUCTIONS, normalize, render
    if contract == "psych-memory-lifecycle-v1":
        from .memory_lifecycle_contract import INSTRUCTIONS as instructions
        from .memory_lifecycle_contract import normalize as normalizer
        from .memory_lifecycle_contract import render as renderer
    elif contract != "psych-memory-v4":
        raise ValueError("unsupported checkpoint contract")
    model_name = report.get("modelName", "psych-memory-qwen4b-lora-20261012")
    if report["status"] != "complete" or report["epochs"] != 1:
        raise ValueError("completed one-epoch report required")
    if (
        hashlib.sha256(
            json.dumps(instructions, sort_keys=True, ensure_ascii=False).encode()
        ).hexdigest()
        != report["datasetManifest"]["instructionsSha256"]
    ):
        raise ValueError("renderer mismatch")
    policies, state, lock = report["taskPolicies"], {}, asyncio.Lock()

    @asynccontextmanager
    async def lifespan(app):
        import torch
        from peft import PeftModel
        from transformers import AutoModelForCausalLM, AutoTokenizer

        if base.name != report["baseRevision"]:
            raise ValueError("base revision mismatch")
        paths = {base / k: v for k, v in report["baseWeightsSha256"].items()}
        paths[checkpoint / "adapter/adapter_model.safetensors"] = report["adapterSha256"]
        for path, expected in paths.items():
            with path.open("rb") as stream:
                if hashlib.file_digest(stream, "sha256").hexdigest() != expected:
                    raise ValueError("weight checksum mismatch")
        torch.set_num_threads(4)
        torch.cuda.set_per_process_memory_fraction(0.20)
        tokenizer = AutoTokenizer.from_pretrained(base, padding_side="left", local_files_only=True)
        model = AutoModelForCausalLM.from_pretrained(
            base, torch_dtype=torch.bfloat16, attn_implementation="sdpa", local_files_only=True
        )
        model = PeftModel.from_pretrained(model, checkpoint / "adapter", is_trainable=False)
        state.update(torch=torch, tokenizer=tokenizer, model=model.to("cuda:0").eval())
        yield
        state.clear()

    app = FastAPI(lifespan=lifespan)

    @app.get("/health")
    def health():
        return {
            "ready": bool(state),
            "profile": "memory",
            "contractVersion": contract,
            "provider": "qwen",
            "model": model_name,
            "experimental": True,
            "servingFeatures": {
                "decisionBatch": True,
                "maxBatch": 16,
                "minimumRawMargin": MINIMUM_RAW_MARGIN,
            },
            "tasks": {t: p["enabled"] for t, p in policies.items()},
            "artifact": {
                "baseRevision": report["baseRevision"],
                "adapterSha256": report["adapterSha256"],
            },
        }

    def predict_items(items):
        torch, tokenizer, model = (state[k] for k in ("torch", "tokenizer", "model"))
        inputs = tokenizer(
            [renderer(task, source) for task, source in items],
            padding=True,
            return_tensors="pt",
            truncation=False,
        )
        if inputs.input_ids.shape[1] > 2048:
            raise ValueError("token budget exceeded")
        yes, no = (tokenizer.convert_tokens_to_ids(t) for t in ("yes", "no"))
        with torch.inference_mode():
            values = (
                model(**inputs.to("cuda:0"), use_cache=False, logits_to_keep=1)
                .logits[:, -1, [no, yes]]
                .float()
                .cpu()
                .tolist()
            )
        answers = []
        for (task, _), (a, b) in zip(items, values, strict=True):
            answers.append(calibrated_answer(policies[task], a, b))
        return answers

    def predict(request):
        items = [(t, request.state) for t in request.tasks]
        values = predict_items(items)
        return {
            "contractVersion": contract,
            "provider": "qwen",
            "model": model_name,
            "answers": dict(zip(request.tasks, values, strict=True)),
        }

    def authorize(authorization, requested_contract):
        key = os.getenv("QWEN_MEMORY_API_KEY", "")
        if not key or not hmac.compare_digest(authorization, "Bearer " + key):
            raise HTTPException(401, "invalid service authorization")
        if requested_contract != contract:
            raise HTTPException(409, "memory contract mismatch")

    @app.post("/v1/decisions")
    async def decide(request: DecisionRequest, authorization: str = Header(default="")):
        authorize(authorization, request.contractVersion)
        if len(set(request.tasks)) != len(request.tasks) or any(
            t not in policies for t in request.tasks
        ):
            raise HTTPException(422, "unsupported memory tasks")
        try:
            for task in request.tasks:
                normalizer(task, request.state)
            async with lock:
                return await asyncio.to_thread(predict, request)
        except (TypeError, ValueError) as exc:
            raise HTTPException(422, "invalid or oversized source state") from exc

    @app.post("/v1/decisions/batch")
    async def decide_batch(request: BatchDecisionRequest, authorization: str = Header(default="")):
        authorize(authorization, request.contractVersion)
        if request.task not in policies:
            raise HTTPException(422, "unsupported memory task")
        try:
            for source in request.states:
                normalizer(request.task, source)
            async with lock:
                values = await asyncio.to_thread(
                    predict_items, [(request.task, source) for source in request.states]
                )
            return {
                "contractVersion": contract,
                "provider": "qwen",
                "model": model_name,
                "task": request.task,
                "answers": [{"index": i, **value} for i, value in enumerate(values)],
            }
        except (TypeError, ValueError) as exc:
            raise HTTPException(422, "invalid or oversized source states") from exc

    return app


if __name__ == "__main__":
    import uvicorn

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", type=Path, required=True)
    parser.add_argument("--checkpoint", type=Path, required=True)
    parser.add_argument("--port", type=int, default=18095)
    args = parser.parse_args()
    uvicorn.run(
        create_app(args.base, args.checkpoint), host="127.0.0.1", port=args.port, access_log=False
    )
