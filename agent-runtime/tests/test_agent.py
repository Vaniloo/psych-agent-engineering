import json
from dataclasses import replace

import pytest

from benchmarks.runtime_fixture import ScriptedModel, context_reply, make_runtime
from psych_agent_runtime.config import Settings
from psych_agent_runtime.schemas import AgentRequest


def request(message="你好", request_id="r1"):
    return AgentRequest(username="alice", sessionId=1, message=message, requestId=request_id)


class Backend:
    """Records capability calls and returns canned results."""

    def __init__(self, results=None, history=()):
        self.calls = []
        self.results = {"get_user_context": context_reply(messages=history),
                        "persist_turn": {"saved": True},
                        "crisis_support": {"reply": "危机回复", "resources": []},
                        "get_support_resources": [{"name": "热线", "type": "phone", "contact": "12356",
                                                   "description": "援助", "urgent": False}],
                        **(results or {})}

    async def __call__(self, name, args):
        self.calls.append((name, args))
        value = self.results[name]
        if isinstance(value, Exception):
            raise value
        return value

    def names(self):
        return [name for name, _ in self.calls]


@pytest.mark.asyncio
async def test_plain_chat_answers_without_tools_and_persists_the_reply():
    backend = Backend()
    model = ScriptedModel(["今天辛苦了。"])
    runtime = make_runtime(model, invoke=backend)
    response = await runtime.invoke(request("今天好累"))
    assert response.reply == "今天辛苦了。"
    assert not response.used_tool
    assert backend.names() == ["get_user_context", "persist_turn"]
    assert backend.calls[-1][1]["assistant_reply"] == "今天辛苦了。"
    # Without a prefilter every tool the account may use is offered to the model.
    assert set(model.requests[0]["tools"]) == {"search_knowledge", "recall_memory", "get_support_resources"}


@pytest.mark.asyncio
async def test_model_writes_its_own_query_and_the_host_binds_identity():
    backend = Backend({"memory_search": {"evidence": [{"id": "m1", "content": "我喜欢听轻音乐",
                                                       "observedAt": "2026-11-01", "sessionId": 3}]}})
    model = ScriptedModel([[("recall_memory", json.dumps({"query": "喜欢的放松方式"}))],
                           "你之前说过喜欢听轻音乐。"])
    runtime = make_runtime(model, invoke=backend)
    response = await runtime.invoke(request("你记得我喜欢怎么放松吗"))
    name, args = backend.calls[1]
    assert name == "memory_search"
    assert args["username"] == "alice" and args["query"] == "喜欢的放松方式"
    tool_message = model.requests[1]["messages"][-1]
    assert tool_message["role"] == "tool" and "轻音乐" in tool_message["content"]
    assert response.reply == "你之前说过喜欢听轻音乐。"
    assert response.tool_name == "recall_memory"


@pytest.mark.asyncio
async def test_model_cannot_choose_identity_or_unknown_tools():
    backend = Backend()
    model = ScriptedModel([[("recall_memory", json.dumps({"query": "x", "username": "bob"})),
                            ("persist_turn", "{}"),
                            ("search_knowledge", "not json")],
                           "好的。"])
    runtime = make_runtime(model, invoke=backend)
    await runtime.invoke(request())
    assert backend.names() == ["get_user_context", "persist_turn"]  # nothing ran for the model
    replies = [m for m in model.requests[1]["messages"] if m["role"] == "tool"]
    assert len(replies) == 3 and all("error" in json.loads(m["content"]) for m in replies)


@pytest.mark.asyncio
async def test_duplicate_calls_are_answered_from_history_and_budget_is_enforced():
    backend = Backend({"search_knowledge": {"hits": []}})
    same = ("search_knowledge", json.dumps({"query": "焦虑"}))
    model = ScriptedModel([[same], [same], [("search_knowledge", json.dumps({"query": "压力"}))], "答复"])
    settings = replace(Settings(executor_provider="rules"), agent_loop_max_tool_calls=1)
    runtime = make_runtime(model, settings=settings, invoke=backend)
    response = await runtime.invoke(request("什么是焦虑"))
    assert backend.names().count("search_knowledge") == 1
    assert response.reply == "答复"
    # Once the budget is spent the model is asked to answer with no tools offered.
    assert model.requests[-1]["tools"] == []


@pytest.mark.asyncio
async def test_failed_tool_becomes_an_observation_and_the_model_still_answers():
    backend = Backend({"search_knowledge": RuntimeError("chroma down")})
    model = ScriptedModel([[("search_knowledge", json.dumps({"query": "失眠"}))], "资料暂时查不到。"])
    response = await make_runtime(model, invoke=backend).invoke(request("失眠怎么办"))
    assert response.reply == "资料暂时查不到。"
    assert "error" in json.loads(model.requests[1]["messages"][-1]["content"])


@pytest.mark.asyncio
async def test_explicit_crisis_skips_the_agent_entirely():
    backend = Backend()
    model = ScriptedModel([])
    response = await make_runtime(model, invoke=backend).invoke(request("我现在就想自杀"))
    assert response.crisis and response.reply == "危机回复"
    assert backend.names() == ["get_user_context", "crisis_support", "persist_turn"]
    assert model.requests == []


@pytest.mark.asyncio
async def test_professional_level_reads_support_resources_before_the_model_speaks():
    backend = Backend()
    model = ScriptedModel(["我在这里陪你，也可以联系 12356。"],
                          risk={"risk_band": "elevated", "needs_professional": True})
    response = await make_runtime(model, invoke=backend).invoke(request("最近真的撑不下去了"))
    assert backend.names()[1] == "get_support_resources"
    first = model.requests[0]["messages"]
    assert first[-1]["role"] == "tool" and "12356" in first[-1]["content"]
    assert response.resources[0].contact == "12356"


@pytest.mark.asyncio
async def test_model_outage_is_reported_honestly_and_turn_is_still_saved():
    backend = Backend()
    runtime = make_runtime(None, invoke=backend)
    response = await runtime.invoke(request())
    assert "暂时无法连接" in response.reply
    assert backend.names()[-1] == "persist_turn"


@pytest.mark.asyncio
async def test_web_search_is_only_offered_to_allowlisted_accounts():
    model = ScriptedModel(["好"])
    settings = replace(Settings(executor_provider="rules"), web_search_enabled=True, web_search_users="alice")
    await make_runtime(model, settings=settings, invoke=Backend()).invoke(request())
    assert "search_web" in model.requests[0]["tools"]
    model = ScriptedModel(["好"])
    settings = replace(settings, web_search_users="bob")
    await make_runtime(model, settings=settings, invoke=Backend()).invoke(request())
    assert "search_web" not in model.requests[0]["tools"]
