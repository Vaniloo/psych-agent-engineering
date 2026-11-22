
import pytest

from benchmarks.public_tool_replay import CATALOG, OWNER, Facade, graph_case, raw_cases, tool_fixture
from psych_agent_runtime.capabilities import CAPABILITIES


class TextScores:
    async def score(self, row, mode):
        return {"scores": [1.0 if c["kind"] == "assistant_text" else -1.0 for c in row["candidateTools"]],
                "durationMs": 0, "maxTokens": 1}


def test_model_catalog_uses_real_tools_but_does_not_expose_identity_as_user_parameter():
    assert set(CATALOG) <= set(CAPABILITIES)
    for tool in CATALOG.values():
        assert tool["description"]
        assert not {"username", "session_id", "request_id", "model_approval"} & set(tool["parameters"]["properties"])
    for case in raw_cases():
        assert set(case["expected"]) <= {*CATALOG, "assistant_text"}


@pytest.mark.asyncio
async def test_text_delegates_read_subloop_using_only_next_action():
    facade = Facade(TextScores(), "candidate")
    facade.loop = {"request": "你好", "results": {}}
    reply = await facade.evaluate({"allowedTools": ["memory_search"]}, ["next_action"])
    assert reply["decisions"]["next_action"]["choice"] == "finish"
    assert facade.records[0]["rawAction"] == "assistant_text"
    with pytest.raises(ValueError, match="next_action"):
        await facade.evaluate({}, ["unsupported"])


@pytest.mark.asyncio
@pytest.mark.parametrize("name,message,route,must", [
    ("chat", "今天想轻松聊两句", "chat", []),
    ("memory", "我最近经常吃什么", "chat", ["memory_search"]),
    ("plan", "帮我创建一个每天阅读的目标", "strategy", ["persist_turn"]),
])
async def test_full_graph_probe_isolated_and_receipt_order_is_preserved(name, message, route, must):
    result = await graph_case(TextScores(), "candidate", name, message, route, must)
    assert result["passed"], result
    if name == "plan":
        retired = {"get_goal_context", "begin_turn", "create_goal", "sync_goal_progress", "manage_goal", "advance_goal"}
        assert retired.isdisjoint(CAPABILITIES)
        assert retired.isdisjoint(call["tool"] for call in result["calls"])
    assert result["realBusinessWrites"] is False
    assert result["generation"] == "fixture_not_quality_evaluated"


@pytest.mark.asyncio
async def test_optional_real_read_gateway_never_receives_persistence():
    seen = []

    async def gateway(name, arguments):
        seen.append(name)
        if name == "risk_scan":
            return {"highRisk": False}
        if name == "get_user_context":
            return {"username": OWNER, "sessionId": 9}
        return tool_fixture(name)

    result = await graph_case(TextScores(), "candidate", "gateway", "我最近经常吃什么", "chat",
                              ["memory_search"], read_gateway=gateway)
    assert result["passed"]
    assert "memory_search" in seen
    assert "persist_turn" not in seen
