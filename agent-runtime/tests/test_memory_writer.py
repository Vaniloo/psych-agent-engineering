import asyncio
import json
from unittest.mock import AsyncMock

import pytest

from psych_agent_runtime.config import Settings
from psych_agent_runtime.memory import MemoryService
from psych_agent_runtime.memory_writer import MemoryWriter, to_update
from psych_agent_runtime.schemas import AgentContext

JOB = {"id": 7, "username": "alice", "sessionId": 1, "requestId": "r7",
       "userMessage": "最近考试压力大，散步能让我放松一点", "assistantReply": "散步是个好办法",
       "createdAt": "2026-11-26T10:00:00+08:00"}


class Model:
    def __init__(self, answer):
        self.answer, self.seen = answer, []

    async def chat_json(self, messages, *, purpose):
        self.seen.append(json.loads(messages[1]["content"]))
        if isinstance(self.answer, Exception):
            raise self.answer
        return self.answer


def backend():
    context = AgentContext(username="alice", sessionId=1, sessionSummary="暂无会话摘要",
                           longTermMemory="喜欢听轻音乐")
    return AsyncMock(context=AsyncMock(return_value=context))


@pytest.mark.asyncio
async def test_turn_is_folded_into_summary_and_profile():
    model = Model({"session_summary": "在聊考试压力", "long_term_memory": "喜欢听轻音乐；散步能放松",
                   "profile": {"coping_strategies": "散步", "concerns": "", "unknown": "x"}})
    update = await MemoryWriter(model, backend()).summarize(JOB)
    assert update == {"sessionSummary": "在聊考试压力", "mediumTermSummary": None,
                      "longTermMemory": "喜欢听轻音乐；散步能放松",
                      "profilePatch": {"coping_strategies": "散步"}}
    sent = model.seen[0]
    assert sent["当前记忆"]["long_term_memory"] == "喜欢听轻音乐"  # merges instead of overwriting
    assert "考试压力" in sent["本轮对话"]["user"]


@pytest.mark.parametrize("answer", [{}, {"session_summary": "  "}, ["not", "a", "dict"], None])
def test_nothing_to_change_yields_no_update(answer):
    assert to_update(answer) is None


@pytest.mark.asyncio
async def test_writer_failure_or_missing_model_skips_the_update():
    assert await MemoryWriter(Model(RuntimeError("down")), backend()).summarize(JOB) is None
    assert await MemoryWriter(None, backend()).summarize(JOB) is None


@pytest.mark.asyncio
async def test_worker_indexes_then_completes_the_job_with_the_update(tmp_path):
    api = AsyncMock()
    api.memory_jobs = AsyncMock(side_effect=[[JOB], asyncio.CancelledError()])
    writer = AsyncMock(summarize=AsyncMock(return_value={"sessionSummary": "在聊考试压力"}))
    service = MemoryService(Settings(memory_store_path=str(tmp_path / "m.db")), api, writer)
    with pytest.raises(asyncio.CancelledError):
        await service._worker()
    api.complete_memory_job.assert_awaited_once_with(7, {"sessionSummary": "在聊考试压力"})
    assert service.store.candidates("alice", "散步")[0]["content"] == JOB["userMessage"]
