from types import SimpleNamespace

import pytest

from psych_agent_runtime.config import Settings
from psych_agent_runtime.graph import AgentGraphRuntime
from psych_agent_runtime.route_policy import plan_for_route
from psych_agent_runtime.token_budget import ContextBudgetManager


@pytest.mark.asyncio
async def test_memory_evidence_survives_large_secondary_goal_result():
    seen = []

    async def stream(messages, **kwargs):
        seen.extend(messages)
        yield "按你的原话回答"

    runtime = AgentGraphRuntime.__new__(AgentGraphRuntime)
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
            "get_goal_context": {"goals": [{"description": "工作目标和历史反馈。" * 1000}]},
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
    assert tool_prompt.index("memory_search") < tool_prompt.index("get_goal_context")
    assert "截断" in tool_prompt
