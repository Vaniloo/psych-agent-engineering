"""Separate local Laya worker; the chat Runtime never imports Torch or loads weights."""

import argparse
import asyncio
import hashlib
import hmac
import os
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel, Field

from .web_filter_contract import CONTRACT, MODEL, REVISION, question


class Passage(BaseModel):
    id: str = Field(min_length=1, max_length=80)
    title: str = Field(default='', max_length=200)
    text: str = Field(max_length=1400)


class ScoreRequest(BaseModel):
    contractVersion: str
    stage: Literal['title', 'chunk']
    query: str = Field(min_length=1, max_length=240)
    purpose: Literal['answer', 'role_voice'] = 'answer'
    candidates: list[Passage] = Field(min_length=1, max_length=32)


def create_app(model_path, device='mps'):
    state = {}; lock = asyncio.Lock()

    @asynccontextmanager
    async def lifespan(app):
        import torch
        from laya import Agent
        torch.set_num_threads(2)
        model = Agent(str(model_path), device=device)
        model.model.eval()
        with (Path(model_path) / 'model.safetensors').open('rb') as stream:
            digest = hashlib.file_digest(stream, 'sha256').hexdigest()
        state.update(agent=model, weightsSha256=digest)
        yield
        state.clear()

    app = FastAPI(lifespan=lifespan)

    @app.get('/health')
    def health():
        return {'ready': bool(state), 'provider': 'laya', 'model': MODEL, 'revision': REVISION,
                'contractVersion': CONTRACT, 'device': str(state['agent'].device) if state else device,
                'weightsSha256': state.get('weightsSha256'), 'finetuned': False,
                'calibratedForWeb': False, 'stages': ['title', 'chunk']}

    def score(request):
        agent = state['agent']; started = time.monotonic()
        states = [{'QUERY': request.query, 'purpose': request.purpose,
                   'TITLE': row.title, 'PASSAGE': row.text} for row in request.candidates]
        # Never silently clip a candidate whose discarded tail could reverse its meaning.
        if any(len(agent.tok.encode(str(row), add_special_tokens=False)) > 850 for row in states):
            raise ValueError('candidate exceeds worker token budget')
        replies = agent.predict_batch(states, question(request.stage), batch_size=4)
        return {'contractVersion': CONTRACT, 'stage': request.stage, 'provider': 'laya',
                'model': MODEL, 'revision': REVISION, 'calibrated': False,
                'scores': [{'id': row.id, 'score': reply['answers']['usefulness']['score'],
                            'probabilities': reply['answers']['usefulness']['probabilities']}
                           for row, reply in zip(request.candidates, replies)],
                'durationMs': round((time.monotonic() - started)*1000, 2)}

    @app.post('/v1/web-context/score')
    async def scores(request: ScoreRequest, authorization: str = Header(default='')):
        key = os.environ.get('WEB_FILTER_SERVICE_KEY', '')
        if not key or not hmac.compare_digest(authorization, 'Bearer '+key):
            raise HTTPException(401, 'invalid service authorization')
        if request.contractVersion != CONTRACT or len({r.id for r in request.candidates}) != len(request.candidates):
            raise HTTPException(422, 'invalid scoring contract')
        if not state:
            raise HTTPException(503, 'model not ready')
        try:
            async with lock:
                return await asyncio.to_thread(score, request)
        except ValueError as exc:
            raise HTTPException(422, 'candidate exceeds worker input budget') from exc

    return app


if __name__ == '__main__':
    import uvicorn
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--model-path', type=Path, required=True)
    parser.add_argument('--device', default='mps')
    parser.add_argument('--port', type=int, default=18106)
    args = parser.parse_args()
    uvicorn.run(create_app(args.model_path, args.device), host='127.0.0.1', port=args.port,
                access_log=False)
