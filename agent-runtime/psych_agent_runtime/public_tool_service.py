"""Authenticated scoring-only worker for the frozen public ToolACE adapter."""

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
from pydantic import BaseModel

from .public_tool_client import CONTRACT


class ScoreRequest(BaseModel):
    contractVersion: str
    row: dict


def validate_row(row):
    context, candidates = row["context"], row["candidateTools"]
    if not 1 <= len(candidates) <= 17 or not 1 <= len(context) <= 32:
        raise ValueError("oversized scoring request")
    if any(e["role"] not in {"user", "assistant", "tool_call", "observation"}
           or not isinstance(e["content"], str) for e in context):
        raise ValueError("invalid context")
    if len(json.dumps(row, ensure_ascii=False)) > 64000:
        raise ValueError("oversized scoring request")
    labels = [c["tool"]["name"] if c["kind"] == "tool" else "assistant_text"
              if c["kind"] == "assistant_text" else None for c in candidates]
    if (None in labels or len(set(labels)) != len(labels) or labels[-1] != "assistant_text"
            or any(not isinstance(label, str) for label in labels)):
        raise ValueError("invalid scoring candidates")
    return labels


def create_app(base, checkpoint):
    # Renderer is shared verbatim with training; no serving-time prompt rewrite.
    from .public_tool_contract import render, specification
    base, checkpoint = Path(base), Path(checkpoint)
    report = json.loads((checkpoint / "training-report.json").read_text())
    if (report["status"] != "complete" or report["contract"] != CONTRACT
            or report["rendererSpec"] != specification() or base.name != report["baseRevision"]):
        raise ValueError("completed matching public tool artifact required")
    key = os.getenv("PUBLIC_TOOL_API_KEY", "")
    if not key:
        raise ValueError("PUBLIC_TOOL_API_KEY required")
    state, lock = {}, asyncio.Lock()
    model_name = "psych-toolace-qwen4b-20261019"

    @asynccontextmanager
    async def lifespan(app):
        import torch
        from peft import PeftModel
        from transformers import AutoModelForCausalLM, AutoTokenizer
        paths = {base / n: sha for n, sha in report["baseWeightsSha256"].items()}
        paths[checkpoint / "adapter/adapter_model.safetensors"] = report["adapterSha256"]
        for path, expected in paths.items():
            with path.open("rb") as stream:
                if hashlib.file_digest(stream, "sha256").hexdigest() != expected:
                    raise ValueError("weight checksum mismatch")
        torch.set_num_threads(4)
        torch.cuda.set_per_process_memory_fraction(0.18)
        tokenizer = AutoTokenizer.from_pretrained(base, padding_side="left", local_files_only=True)
        model = AutoModelForCausalLM.from_pretrained(base, local_files_only=True,
            torch_dtype=torch.bfloat16, attn_implementation="sdpa")
        model = PeftModel.from_pretrained(model, checkpoint / "adapter").to("cuda:0").eval()
        state.update(torch=torch, tokenizer=tokenizer, model=model)
        yield
        state.clear()

    app = FastAPI(lifespan=lifespan)

    @app.get("/health")
    def health():
        return {"ready": bool(state), "profile": "executor", "contractVersion": CONTRACT,
                "provider": "public_qwen", "model": model_name, "experimental": True,
                "tasks": {"next_action": True}, "servingFeatures": {
                    "decisionPolicy": "constrained_argmax_with_TEXT", "calibrated": False,
                    "maxTokens": 4096, "concurrentInference": 1, "overflow": "429_then_fallback"},
                "artifact": {"baseRevision": report["baseRevision"], "adapterSha256": report["adapterSha256"]}}

    def predict(row):
        torch, tokenizer, model = (state[k] for k in ("torch", "tokenizer", "model"))
        tokens = tokenizer([render(row, i) for i in range(len(row["candidateTools"]))],
                           truncation=False, padding=False, add_special_tokens=False)
        if max(map(len, tokens["input_ids"])) > 4096:
            raise ValueError("token budget exceeded; no silent truncation")
        encoded = [{k: v[i] for k, v in tokens.items()} for i in range(len(row["candidateTools"]))]
        no, yes = [tokenizer.convert_tokens_to_ids(s) for s in ("no", "yes")]
        scores = []
        with torch.inference_mode():
            for i in range(0, len(encoded), 4):
                batch = tokenizer.pad(encoded[i:i + 4], padding=True, return_tensors="pt").to("cuda:0")
                logits = model(**batch, use_cache=False, logits_to_keep=1).logits[:, -1, [no, yes]].float()
                scores.extend((logits[:, 1] - logits[:, 0]).cpu().tolist())
        if not all(math.isfinite(v) for v in scores):
            raise ValueError("nonfinite scores")
        return scores

    @app.post("/v1/tool-scores")
    async def score(request: ScoreRequest, authorization: str = Header(default="")):
        if not hmac.compare_digest(authorization, "Bearer " + key):
            raise HTTPException(401, "invalid service authorization")
        if request.contractVersion != CONTRACT:
            raise HTTPException(409, "public tool contract mismatch")
        try:
            labels = validate_row(request.row)
        except (KeyError, TypeError, ValueError):
            raise HTTPException(422, "invalid scoring catalog or context")
        if lock.locked():
            raise HTTPException(429, "tool scorer busy; use existing fallback")
        async with lock:
            # Cancellation cannot release the GPU lock while the thread still
            # executes: one worker must never start overlapping CUDA forwards.
            task = asyncio.create_task(asyncio.to_thread(predict, request.row))
            try:
                scores = await asyncio.shield(task)
            except asyncio.CancelledError:
                await task
                raise
            except (KeyError, TypeError, ValueError):
                raise HTTPException(422, "invalid or oversized scoring state")
        return {"contractVersion": CONTRACT, "model": model_name, "candidateIds": labels,
                "scores": scores, "adapterSha256": report["adapterSha256"], "calibrated": False}

    return app


if __name__ == "__main__":
    import uvicorn
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", type=Path, required=True)
    parser.add_argument("--checkpoint", type=Path, required=True)
    parser.add_argument("--port", type=int, required=True)
    args = parser.parse_args()
    uvicorn.run(create_app(args.base, args.checkpoint), host="127.0.0.1", port=args.port, access_log=False)
