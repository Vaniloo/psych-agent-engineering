from types import SimpleNamespace

import pytest

from benchmarks.runtime_fixture import make_runtime

from psych_agent_runtime.config import Settings
from psych_agent_runtime.route_policy import plan_for_route
from psych_agent_runtime.token_budget import ContextBudgetManager


@pytest.mark.asyncio
@pytest.mark.parametrize("route", ["memory"])
async def test_factual_read_does_not_reuse_old_assistant_errors_or_derived_summaries(route):
    captured = []

    async def stream(messages, **kwargs):
        captured.extend(messages)
        yield "依据新证据"

    runtime = make_runtime()
    runtime.settings = Settings()
    runtime.context_budget = ContextBudgetManager(8192, 1200)
    runtime.ollama = SimpleNamespace(stream_chat=stream)
    runtime._emit = lambda *_: None
    state = {
        "context": {
            "username": "alice",
            "sessionId": 1,
            "sessionSummary": "过期摘要：用户没有音乐偏好。",
            "messages": [{"role": "assistant", "content": "错误旧回复：没有任何记录。"}],
        },
        "message": "请按本轮记录回答。",
        "request_id": "fresh-evidence",
        "plan": plan_for_route(route, "问题", "test").model_dump(),
        "tool_results": {
            "memory_search": {
                "status": "controlled",
                "evidence": [{"role": "user", "content": "我喜欢听笛子音乐。"}],
            }
        },
    }
    await runtime._prepare_response(state)
    result = await runtime._respond(state)
    prompt = "\n".join(m["content"] for m in captured)
    assert result["reply"] == "依据新证据"
    assert "我喜欢听笛子音乐。" in prompt
    assert "错误旧回复" not in prompt and "过期摘要" not in prompt


@pytest.mark.asyncio
async def test_memory_evidence_survives_large_secondary_report_result():
    seen = []

    async def stream(messages, **kwargs):
        seen.extend(messages)
        yield "按你的原话回答"

    runtime = make_runtime()
    runtime.settings = Settings()
    runtime.context_budget = ContextBudgetManager(8192, 1200)
    runtime.ollama = SimpleNamespace(stream_chat=stream)
    runtime._emit = lambda *_: None
    state = {
        "context": {"username": "alice", "sessionId": 1},
        "message": "我以前更喜欢怎样整理步骤？",
        "request_id": "evidence-budget",
        "plan": plan_for_route("memory", "问题", "test").model_dump(),
        "tool_results": {
            "get_dashboard": {"reports": [{"description": "历史对话报告。" * 1000}]},
            "memory_search": {
                "evidence": [{"content": "我更喜欢一次只写一句话。", "role": "user"}],
                "status": "controlled",
            },
        },
    }
    result = await runtime._respond(state)
    assert result["reply"] == "按你的原话回答"
    tool_prompt = next(m["content"] for m in seen if "<untrusted_tool_results>" in m["content"])
    assert "我更喜欢一次只写一句话。" in tool_prompt
    assert tool_prompt.index("memory_search") < tool_prompt.index("get_dashboard")
    assert "截断" in tool_prompt


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "message",
    [
        "请解释这句话是什么意思：‘你记得我喜欢什么吗？’不用查询我的记录。",
        "先根据以前的记录告诉我喜欢听什么，再用一句话解释CBT。",
    ],
)
async def test_self_contained_requests_do_not_answer_stale_history_questions(message):
    captured = []

    async def stream(messages, **kwargs):
        captured.extend(messages)
        yield "当前问题"

    runtime = make_runtime()
    runtime.settings = Settings()
    runtime.context_budget = ContextBudgetManager(8192, 1200)
    runtime.ollama = SimpleNamespace(stream_chat=stream)
    runtime._emit = lambda *_: None
    state = {
        "context": {
            "username": "alice",
            "sessionId": 1,
            "messages": [
                {"role": "user", "content": "过期问题：我在哪里休息？"},
                {"role": "assistant", "content": "旧答：好像是在客厅。"},
            ],
        },
        "message": message,
        "request_id": "current-only",
        "plan": plan_for_route("knowledge", message, "test").model_dump(),
        "tool_results": {},
    }
    await runtime._respond(state)
    prompt = "\n".join(m["content"] for m in captured)
    assert "过期问题" not in prompt and "旧答" not in prompt
