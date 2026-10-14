"""Experimental role-specific Qwen4B worker with the existing finite-choice API."""

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

from .decision_contract import profile_contract
from .qwen_choice_contract import choices, normalize, render, renderer_spec


class ChoiceRequest(BaseModel):
    contractVersion: str
    state: dict
    tasks: list[str] = Field(min_length=1, max_length=4)


def choice_answer(labels, scores, policy):
    if (
        len(labels) != len(scores)
        or not labels
        or len(set(labels)) != len(labels)
        or not all(math.isfinite(v) for v in scores)
    ):
        raise ValueError("invalid finite choice scores")
    peak = max(scores)
    values = [math.exp((v - peak) / policy["temperature"]) for v in scores]
    total = sum(values)
    probabilities = {label: value / total for label, value in zip(labels, values, strict=True)}
    choice = max(probabilities, key=probabilities.__getitem__)
    ordered = sorted(scores, reverse=True)
    margin = ordered[0] - ordered[1] if len(ordered) > 1 else math.inf
    stable = margin >= policy.get("minimumRawMargin", 2)
    return {
        "choice": choice,
        "probabilities": probabilities,
        "threshold": policy["threshold"],
        "calibrated": policy["calibrated"],
        "accepted": policy["enabled"] and stable and probabilities[choice] >= policy["threshold"],
        "validationScope": policy["validationScope"],
        "stabilityMargin": margin if math.isfinite(margin) else None,
        "abstentionReason": None if stable else "small_raw_logit_margin",
    }


def create_app(base, checkpoint, profile):
    base, checkpoint = Path(base), Path(checkpoint)
    report = json.loads((checkpoint / "training-report.json").read_text())
    if report["profile"] != profile or report["status"] != "complete" or report["epochs"] != 1:
        raise ValueError("completed one-epoch role report required")
    fingerprint = hashlib.sha256(
        json.dumps(renderer_spec(profile), ensure_ascii=False, sort_keys=True).encode()
    ).hexdigest()
    if fingerprint != report["rendererSha256"]:
        raise ValueError("choice renderer mismatch")
    contract, questions = profile_contract(profile)
    policies = report["taskPolicies"]
    state = {}
    lock = asyncio.Lock()
    if set(policies) != set(questions):
        raise ValueError("incomplete task policy")

    @asynccontextmanager
    async def lifespan(app):
        import torch
        from peft import PeftModel
        from transformers import AutoModelForCausalLM, AutoTokenizer

        if base.name != report["baseRevision"]:
            raise ValueError("base revision mismatch")
        paths = {base / name: expected for name, expected in report["baseWeightsSha256"].items()}
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
            "profile": profile,
            "contractVersion": contract,
            "provider": "qwen",
            "model": f"psych-{profile}-qwen4b-lora-20261012",
            "experimental": True,
            "tasks": {task: p["enabled"] for task, p in policies.items()},
            "servingFeatures": {"finiteChoiceBatch": True, "minimumRawMargin": 2},
            "artifact": {
                "baseRevision": report["baseRevision"],
                "adapterSha256": report["adapterSha256"],
            },
        }

    def predict(request):
        torch, tokenizer, model = (state[k] for k in ("torch", "tokenizer", "model"))
        groups = []
        texts = []
        for task in request.tasks:
            source = normalize(profile, task, request.state)
            labels = list(choices(profile, task, source))
            groups.append((task, labels))
            texts.extend(render(profile, task, source, label) for label in labels)
        if len(texts) > 16:
            raise ValueError("choice batch budget exceeded")
        inputs = tokenizer(texts, padding=True, return_tensors="pt", truncation=False)
        if inputs.input_ids.shape[1] > 2048:
            raise ValueError("token budget exceeded")
        no, yes = (tokenizer.convert_tokens_to_ids(t) for t in ("no", "yes"))
        with torch.inference_mode():
            logits = (
                model(**inputs.to("cuda:0"), use_cache=False, logits_to_keep=1)
                .logits[:, -1, [no, yes]]
                .float()
            )
            values = (logits[:, 1] - logits[:, 0]).cpu().tolist()
        answers = {}
        offset = 0
        for task, labels in groups:
            answers[task] = choice_answer(
                labels, values[offset : offset + len(labels)], policies[task]
            )
            offset += len(labels)
        return {
            "contractVersion": contract,
            "provider": "qwen",
            "model": f"psych-{profile}-qwen4b-lora-20261012",
            "answers": answers,
        }

    @app.post("/v1/decisions")
    async def decide(request: ChoiceRequest, authorization: str = Header(default="")):
        key = os.getenv("QWEN_CHOICE_API_KEY", "")
        if not key or not hmac.compare_digest(authorization, "Bearer " + key):
            raise HTTPException(401, "invalid service authorization")
        if request.contractVersion != contract:
            raise HTTPException(409, "choice contract mismatch")
        if len(set(request.tasks)) != len(request.tasks) or any(
            t not in policies for t in request.tasks
        ):
            raise HTTPException(422, "unsupported role tasks")
        try:
            for task in request.tasks:
                normalize(profile, task, request.state)
            async with lock:
                return await asyncio.to_thread(predict, request)
        except (TypeError, ValueError, KeyError) as exc:
            raise HTTPException(422, "invalid or oversized choice state") from exc

    return app


if __name__ == "__main__":
    import uvicorn

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", choices=["router", "executor"], required=True)
    parser.add_argument("--base", type=Path, required=True)
    parser.add_argument("--checkpoint", type=Path, required=True)
    parser.add_argument("--port", type=int, required=True)
    args = parser.parse_args()
    uvicorn.run(
        create_app(args.base, args.checkpoint, args.profile),
        host="127.0.0.1",
        port=args.port,
        access_log=False,
    )
