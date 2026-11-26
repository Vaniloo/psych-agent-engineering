"""Graph test harness: a runtime with fake dependencies and no network clients."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

from psych_agent_runtime.config import Settings
from psych_agent_runtime.graph import AgentGraphRuntime
from psych_agent_runtime.llm import StepResult, ToolCall
from psych_agent_runtime.prefilter import ToolPrefilter
from psych_agent_runtime.token_budget import ContextBudgetManager


class ScriptedModel:
    """Plays back assistant steps in order. Each step is text or a list of tool calls."""

    model = "scripted"

    def __init__(self, steps, risk=None):
        self.steps = list(steps)
        self.risk = risk or {"risk_band": "low", "needs_professional": False}
        self.requests = []

    async def chat_json(self, messages, *, purpose):
        return self.risk

    async def stream_step(self, messages, tools, on_text, *, purpose="agent_step"):
        self.requests.append({"messages": [dict(m) for m in messages],
                              "tools": [t["function"]["name"] for t in tools or []]})
        step = self.steps.pop(0)
        if isinstance(step, str):
            on_text(step)
            return StepResult(content=step, finish_reason="stop")
        return StepResult(tool_calls=[ToolCall(f"call_{i}", name, args)
                                      for i, (name, args) in enumerate(step)],
                          finish_reason="tool_calls")


def make_runtime(model=None, *, settings=None, invoke=None):
    runtime = AgentGraphRuntime.__new__(AgentGraphRuntime)
    runtime.settings = settings or Settings(executor_provider="rules", checkpoint_backend="memory")
    runtime.model = model
    runtime.prefilter = ToolPrefilter(runtime.settings)
    runtime.context_budget = ContextBudgetManager(8192, 1200)
    runtime.tools = SimpleNamespace(call=invoke or AsyncMock(return_value={}))
    runtime._emit = lambda *args: None
    runtime.graph = runtime._build_graph().compile()
    return runtime


def context_reply(username="alice", session_id=1, messages=()):
    return {"username": username, "sessionId": session_id, "messages": list(messages)}
