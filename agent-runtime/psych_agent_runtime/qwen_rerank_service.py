"""Local immutable Qwen 0.6B reranking worker; no fine-tuning or business writes."""

import argparse
import asyncio
import hashlib
import hmac
import json
import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel, Field

from .qwen_memory_contract import normalize, render


class RerankRequest(BaseModel):
    query: str = Field(max_length=4000)
    candidates: list[dict] = Field(min_length=1, max_length=32)


def create_app(base, report_path, device="cuda:0"):
    lock = asyncio.Lock()
    state = {}

    @asynccontextmanager
    async def lifespan(app):
        import torch
        from transformers import AutoModelForCausalLM, AutoTokenizer

        report = json.loads(Path(report_path).read_text())
        if report["model"] != "Qwen/Qwen3-Reranker-0.6B" or Path(base).name != report["revision"]:
            raise ValueError("reranker must use the pinned original 0.6B snapshot")
        for name, expected in report["weightsSha256"].items():
            with (Path(base) / name).open("rb") as stream:
                if hashlib.file_digest(stream, "sha256").hexdigest() != expected:
                    raise ValueError("original reranker weights changed")
        torch.set_num_threads(4)
        torch.cuda.set_per_process_memory_fraction(0.08, device)
        state.update(
            torch=torch,
            tokenizer=AutoTokenizer.from_pretrained(
                base, padding_side="left", local_files_only=True
            ),
            model=AutoModelForCausalLM.from_pretrained(
                base,
                torch_dtype=torch.bfloat16,
                attn_implementation="sdpa",
                local_files_only=True,
            )
            .to(device)
            .eval(),
            report=report,
        )
        yield
        state.clear()

    app = FastAPI(lifespan=lifespan)

    @app.get("/health")
    def health():
        return {
            "ready": bool(state),
            "model": "Qwen/Qwen3-Reranker-0.6B",
            "revision": state.get("report", {}).get("revision"),
            "role": "rerank_only",
            "finetuned": False,
            "maxCandidates": 32,
        }

    def score(request):
        torch, tokenizer, model = (state[k] for k in ("torch", "tokenizer", "model"))
        texts = [
            render("relevance", {"query": request.query, "candidate": c})
            for c in request.candidates
        ]
        # Bound total work and GPU peak; a larger recall pool is scored in chunks.
        if any(len(tokenizer.encode(text)) > 2048 for text in texts):
            raise ValueError("rerank token budget exceeded")
        yes, no = (tokenizer.convert_tokens_to_ids(t) for t in ("yes", "no"))
        values = []
        with torch.inference_mode():
            for offset in range(0, len(texts), 8):
                inputs = tokenizer(
                    texts[offset : offset + 8], padding=True, return_tensors="pt", truncation=False
                )
                logits = model(**inputs.to(device), use_cache=False, logits_to_keep=1).logits[
                    :, -1, [no, yes]
                ]
                values.extend(torch.softmax(logits.float(), -1)[:, 1].cpu().tolist())
        ranking = [{"index": i, "score": value} for i, value in enumerate(values)]
        return {
            "model": "Qwen/Qwen3-Reranker-0.6B",
            "ranking": sorted(ranking, key=lambda r: (-r["score"], r["index"])),
            "calibrated": False,
            "role": "rerank_only",
        }

    @app.post("/v1/rerank")
    async def rerank(request: RerankRequest, authorization: str = Header(default="")):
        key = os.environ.get("QWEN_RERANK_API_KEY", "")
        if not key or not hmac.compare_digest(authorization, "Bearer " + key):
            raise HTTPException(401, "invalid service authorization")
        try:
            for c in request.candidates:
                normalize("relevance", {"query": request.query, "candidate": c})
            async with lock:
                return await asyncio.to_thread(score, request)
        except (ValueError, TypeError) as exc:
            raise HTTPException(422, "invalid or oversized source records") from exc

    return app


if __name__ == "__main__":
    import uvicorn

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", type=Path, required=True)
    parser.add_argument("--base-report", type=Path, required=True)
    parser.add_argument("--port", type=int, default=18094)
    args = parser.parse_args()
    uvicorn.run(
        create_app(args.base, args.base_report), host="127.0.0.1", port=args.port, access_log=False
    )
