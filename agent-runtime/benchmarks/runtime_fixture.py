"""Graph node test harness: explicit dependencies, no network clients or startup."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

from psych_agent_runtime.config import Settings
from psych_agent_runtime.graph import AgentGraphRuntime
from psych_agent_runtime.token_budget import ContextBudgetManager
from psych_agent_runtime.tool_controller import ToolController


def risk_client(band="low", professional=False):
    return SimpleNamespace(model="fixture", chat_json=AsyncMock(return_value={
        "risk_band": band, "support_need": "professional" if professional else "general"}))


def make_runtime():
    runtime = AgentGraphRuntime.__new__(AgentGraphRuntime)
    runtime.settings = Settings()
    runtime.backend = SimpleNamespace(model_api=AsyncMock(return_value={}))
    runtime.decision_client = risk_client()
    runtime.ollama = None
    runtime.tool_controller = ToolController(runtime.settings)
    runtime.context_budget = ContextBudgetManager(8192, 1200)
    runtime.tools = SimpleNamespace()
    runtime.memory = SimpleNamespace(store=SimpleNamespace(
        has_other_session=Mock(return_value=False), record_read_audit=Mock(),
    ))
    return runtime
