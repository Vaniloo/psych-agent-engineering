from unittest.mock import AsyncMock

import pytest

from psych_agent_runtime.config import Settings
from psych_agent_runtime.memory import MemoryService

VECTORS = {  # tiny fake embedding space: [relax, food, exam]
    "我喜欢听轻音乐放松": [1.0, 0.0, 0.0],
    "我今天中午吃了面": [0.0, 1.0, 0.0],
    "下周要考试了": [0.0, 0.0, 1.0],
    "我平时怎么放松": [0.9, 0.0, 0.1],
}


class Embedder:
    def __init__(self, broken=False):
        self.broken = broken

    async def embed(self, texts, purpose):
        if self.broken:
            raise RuntimeError("ollama down")
        return [VECTORS.get(t, [0.0, 0.0, 0.0]) for t in texts]


def job(i, text, session=1):
    return {"username": "alice", "sessionId": session, "requestId": f"r{i}", "userMessage": text,
            "assistantReply": "好的", "createdAt": f"2026-11-2{i}T10:00:00+08:00"}


async def service(tmp_path, embedder, backend=None):
    memory = MemoryService(Settings(memory_store_path=str(tmp_path / "m.db")), backend, embedder=embedder)
    for i, text in enumerate(["我喜欢听轻音乐放松", "我今天中午吃了面", "下周要考试了"], start=1):
        await memory.ingest(job(i, text))
    return memory


@pytest.mark.asyncio
async def test_vector_recall_finds_a_paraphrase_with_no_shared_words(tmp_path):
    memory = await service(tmp_path, Embedder())
    result = await memory.retrieve("alice", "我平时怎么放松", limit=2)
    assert result["status"] == "ranked"
    assert result["evidence"][0]["content"] == "我喜欢听轻音乐放松"
    assert "embedding" not in result["evidence"][0]


@pytest.mark.asyncio
async def test_recent_messages_do_not_override_relevance(tmp_path):
    fresh = [{"id": "message:9", "content": "刚刚说的一句无关的话", "observedAt": "2026-11-30T10:00:00+08:00",
              "timestampRole": "observation_time", "sessionId": 2, "source": "message:9", "role": "user"}]
    backend = AsyncMock(recent_messages=AsyncMock(return_value=fresh))
    memory = await service(tmp_path, Embedder(), backend)
    result = await memory.retrieve("alice", "我平时怎么放松", limit=2, recent_messages=True)
    assert result["evidence"][0]["content"] == "我喜欢听轻音乐放松"
    assert result["retrievalCoverage"]["freshMessages"] == 1


@pytest.mark.asyncio
async def test_embedding_outage_falls_back_to_bm25_then_to_latest(tmp_path):
    memory = await service(tmp_path, Embedder(broken=True))
    lexical = await memory.retrieve("alice", "考试")
    assert lexical["evidence"][0]["content"] == "下周要考试了" and not lexical["retrievalCoverage"]["vector"]
    nothing = await memory.retrieve("alice", "完全无关", limit=2)
    assert nothing["status"] == "recent_only" and len(nothing["evidence"]) == 2


@pytest.mark.asyncio
async def test_other_accounts_are_never_in_the_pool(tmp_path):
    memory = await service(tmp_path, Embedder())
    assert (await memory.retrieve("bob", "放松"))["evidence"] == []
