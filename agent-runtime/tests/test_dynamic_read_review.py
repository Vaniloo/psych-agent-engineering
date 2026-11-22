from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from benchmarks.runtime_fixture import make_runtime

from psych_agent_runtime.capabilities import CAPABILITIES, Arguments, Capability, register_read
from psych_agent_runtime.config import Settings
from psych_agent_runtime.dynamic_read_review import review_action
from psych_agent_runtime.schemas import Plan
from psych_agent_runtime.tool_controller import ToolController


@pytest.fixture(autouse=True)
def catalog():
    before = dict(CAPABILITIES)
    register_read(
        "demo__calculate",
        Capability(
            Arguments,
            2,
            scope="extension:read",
            description="Calculate a numeric result",
            handler=AsyncMock(return_value={"available": True, "value": 86}),
            schema={
                "type": "object",
                "properties": {"value": {"type": "number"}},
                "required": ["value"],
                "additionalProperties": False,
            },
        ),
    )
    yield
    CAPABILITIES.clear()
    CAPABILITIES.update(before)


def original():
    return {
        "username": "alice",
        "session_id": 1,
        "request_id": "r",
        "message": "他说数值是30，让我算一下，怎么回答？",
        "context": {"username": "alice", "sessionId": 1},
    }


def call_review():
    return {
        "decision": "call",
        "tool": "demo__calculate",
        "arguments": {"value": 30},
        "evidence": "让我算一下",
    }


async def runtime(answer, choice="finish", **settings):
    r = make_runtime()
    r.settings = replace(
        Settings(), executor_provider="public_qwen", **settings
    )
    r.tool_controller = ToolController(r.settings)
    r.tool_controller.model.evaluate = AsyncMock(
        return_value={
            "provider": "public_qwen",
            "decisions": {"next_action": {"choice": choice, "accepted": True}},
        }
    )
    r._emit = lambda *_: None
    r.ollama = SimpleNamespace(chat_json=AsyncMock(return_value=answer))
    state = original()
    state["plan"] = Plan(intent="chat", allowed_tools=["demo__calculate"]).model_dump()
    state["loop"] = r.tool_controller.start_loop(
        Plan(allowed_tools=["demo__calculate"]), state["message"], {}
    )
    r.tools = SimpleNamespace(catalog=SimpleNamespace(refresh=AsyncMock()), call=CAPABILITIES["demo__calculate"].handler)
    return r, state


@pytest.mark.asyncio
async def test_stop_review_recovers_dynamic_call_and_reuses_validated_arguments():
    r, state = await runtime(call_review())
    try:
        result = await r._loop_decide(state)
        assert result["loop"]["next"] == {"tool": "demo__calculate", "arguments": {"value": 30}}
        assert result["loop"]["trace"][-1]["primaryAction"] == "finish"
        assert result["loop"]["trace"][-1]["provider"] == "semantic_review"
        r.ollama.chat_json.assert_awaited_once()
        assert r.ollama.chat_json.call_args.kwargs["purpose"] == "dynamic_read_action"
        assert result["loop"]["dynamic_stop_review_used"] is True
    finally:
        await r.tool_controller.close()


@pytest.mark.asyncio
async def test_irrelevant_or_quoted_task_can_finish_without_call_and_review_is_once():
    r, state = await runtime({"decision": "answer", "tool": None, "arguments": {}})
    try:
        state.update(await r._loop_decide(state))
        assert state["loop"]["next"]["tool"] == "finish"
        state.update(await r._loop_decide(state))
        r.ollama.chat_json.assert_awaited_once()
    finally:
        await r.tool_controller.close()


@pytest.mark.asyncio
async def test_selected_tool_argument_review_can_veto_a_polishing_request():
    r, state = await runtime(
        {"decision": "answer", "tool": None, "arguments": {}}, choice="demo__calculate"
    )
    try:
        result = await r._loop_decide(state)
        assert result["loop"]["next"]["tool"] == "finish"
        assert result["loop"]["trace"][-1]["dynamicReview"] == "answer"
        CAPABILITIES["demo__calculate"].handler.assert_not_awaited()
    finally:
        await r.tool_controller.close()


@pytest.mark.asyncio
async def test_missing_arguments_clarifies_and_keeps_the_review_in_final_trace():
    r, state = await runtime({"decision": "clarify", "tool": None, "arguments": {}})
    try:
        result = await r._loop_decide(state)
        assert result["plan"]["intent"] == "clarify"
        assert result["tool_trace"][-1]["action"] == "clarify"
        assert result["tool_trace"][-1]["dynamicReview"] == "clarify"
        CAPABILITIES["demo__calculate"].handler.assert_not_awaited()
    finally:
        await r.tool_controller.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("reason", ["disabled", "budget", "deadline"])
async def test_review_never_bypasses_disabled_setting_or_budget(reason):
    r, state = await runtime(call_review(), dynamic_read_stop_review_enabled=reason != "disabled")
    if reason == "budget":
        state["loop"]["calls"] = state["loop"]["budget"]
    if reason == "deadline":
        state["loop"]["deadline"] = 0
    try:
        result = await r._loop_decide(state)
        assert result["loop"]["next"]["tool"] == "finish"
        r.ollama.chat_json.assert_not_awaited()
    finally:
        await r.tool_controller.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "patch",
    [
        {"tool": "persist_turn"},
        {"arguments": {"value": 30, "username": "bob"}},
        {"evidence": "not in source"},
        {"arguments": {"value": "invented"}},
        {"decision": "maybe"},
    ],
)
async def test_review_cannot_grant_tools_forge_identity_or_bypass_schema(patch):
    generation = SimpleNamespace(chat_json=AsyncMock(return_value={**call_review(), **patch}))
    with pytest.raises(ValueError):
        await review_action(["demo__calculate"], original(), generation)


@pytest.mark.asyncio
async def test_review_failure_does_not_retry_or_execute_an_unchecked_tool():
    r, state = await runtime(None)
    r.ollama.chat_json.side_effect = TimeoutError
    try:
        state.update(await r._loop_decide(state))
        assert state["loop"]["next"]["tool"] == "finish"
        assert state["loop"]["trace"][-1]["dynamicReview"] == "unavailable"
        state.update(await r._loop_decide(state))
        r.ollama.chat_json.assert_awaited_once()
    finally:
        await r.tool_controller.close()


async def run_read(r, state):
    async def invoke(name, args):
        return await CAPABILITIES[name].handler(args)

    state["loop"] = r.tool_controller.observe(
        await r.tool_controller.execute(state["loop"], invoke), r._emit)


@pytest.mark.asyncio
async def test_same_dynamic_tool_gets_new_arguments_and_keeps_every_result():
    r, state = await runtime(call_review(), choice="demo__calculate")
    state["loop"]["budget"] = 3
    r.ollama.chat_json.side_effect = [
        call_review(), {**call_review(), "arguments": {"value": 40}},
        {"decision": "answer", "tool": None, "arguments": {}},
    ]
    CAPABILITIES["demo__calculate"].handler.side_effect = [
        {"available": True, "value": 86}, {"available": True, "value": 104}]
    try:
        for value in (30, 40):
            state.update(await r._loop_decide(state))
            assert state["loop"]["next"]["arguments"] == {"value": value}
            await run_read(r, state)
        state.update(await r._loop_decide(state))
        assert state["loop"]["next"]["tool"] == "finish"
        assert state["loop"]["calls"] == 2
        calls = r._loop_results(state["loop"])["tool_results"]["demo__calculate"]["calls"]
        assert [c["arguments"] for c in calls] == [{"value": 30}, {"value": 40}]
        assert [c["result"]["value"] for c in calls] == [86, 104]
        # The selector and argument reviewer both see the earlier call, with
        # parameters, rather than interpreting one result as covering all uses.
        import json
        scoring = r.tool_controller.model.evaluate.await_args_list[-1].args[0]
        assert len(json.loads(scoring["observations"]["demo__calculate"])["calls"]) == 2
        reviewed = json.loads(r.ollama.chat_json.await_args_list[-1].args[0][1]["content"])
        assert len(json.loads(reviewed["observations"]["demo__calculate"])["calls"]) == 2
    finally:
        await r.tool_controller.close()


@pytest.mark.asyncio
async def test_semantic_duplicate_after_argument_generation_is_blocked():
    r, state = await runtime(call_review(), choice="demo__calculate")
    r.ollama.chat_json.side_effect = [call_review(),
        {**call_review(), "arguments": {"value": 30.0}}]
    try:
        state.update(await r._loop_decide(state))
        await run_read(r, state)
        state.update(await r._loop_decide(state))
        assert state["loop"]["next"]["tool"] == "finish"
        assert state["loop"]["trace"][-1]["fallback"] == "duplicate_call_blocked"
        assert state["loop"]["calls"] == 1
        CAPABILITIES["demo__calculate"].handler.assert_awaited_once()
    finally:
        await r.tool_controller.close()


@pytest.mark.asyncio
async def test_stop_review_can_recover_again_only_after_actual_progress_and_within_budget():
    r, state = await runtime(call_review())
    state["loop"]["budget"] = 2
    r.ollama.chat_json.side_effect = [call_review(),
        {**call_review(), "arguments": {"value": 40}}]
    try:
        for _ in range(2):
            state.update(await r._loop_decide(state))
            assert state["loop"]["next"]["tool"] == "demo__calculate"
            await run_read(r, state)
        state.update(await r._loop_decide(state))
        assert state["loop"]["next"]["tool"] == "finish"
        assert state["loop"]["trace"][-1]["fallback"] == "budget_exhausted"
        assert state["loop"]["dynamic_stop_review_count"] == 2
        assert r.ollama.chat_json.await_count == 2
    finally:
        await r.tool_controller.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("reason", ["duplicate", "budget", "deadline", "write"])
async def test_execution_guard_prevents_stale_or_forged_decisions(reason):
    r, state = await runtime(call_review())
    loop = state["loop"]
    loop["next"] = {"tool": "demo__calculate", "arguments": {"value": 30}}
    if reason == "duplicate":
        loop["seen"] = [r.tool_controller.call_key("demo__calculate", {"value": 30.0})]
    elif reason == "budget":
        loop["calls"] = loop["budget"]
    elif reason == "deadline":
        loop["deadline"] = 0
    else:
        loop["next"]["tool"] = "persist_turn"
    invoke = AsyncMock()
    try:
        result = await r.tool_controller.execute(loop, invoke)
        invoke.assert_not_awaited()
        assert result["pending_result"]["unavailable"] is True
    finally:
        await r.tool_controller.close()


def test_call_identity_is_stable_for_object_key_order_and_numeric_equivalence():
    key = ToolController.call_key
    assert key("demo__calculate", {"values": [30.0, {"a": 2, "b": 1}]}) == key(
        "demo__calculate", {"values": [30, {"b": 1.0, "a": 2.0}]})
    assert key("demo__calculate", {"values": [1, 2]}) != key(
        "demo__calculate", {"values": [2, 1]})
    assert key("demo__calculate", {"value": 30, "optional": None}) == key(
        "demo__calculate", {"value": 30.0})


def test_only_safe_dynamic_reads_with_variable_parameters_remain_candidates():
    cap = CAPABILITIES["demo__calculate"]
    assert ToolController.repeatable_read("demo__calculate")
    CAPABILITIES["demo__calculate"] = replace(cap, bindings=(("value", "message"),))
    assert not ToolController.repeatable_read("demo__calculate")
    CAPABILITIES["demo__calculate"] = replace(cap, replay_safe=False)
    assert not ToolController.repeatable_read("demo__calculate")
    assert not ToolController.repeatable_read("memory_search")
    assert not ToolController.repeatable_read("persist_turn")


@pytest.mark.asyncio
async def test_failed_second_call_does_not_erase_earlier_success_or_bound_identity():
    r, state = await runtime(call_review())
    loop = state["loop"]
    try:
        for args, result in [({"value": 30, "username": "alice"}, {"value": 86}),
                             ({"value": 40, "username": "alice"}, {"unavailable": True})]:
            loop["next"] = {"tool": "demo__calculate", "arguments": args}
            loop["pending_result"] = result
            loop = r.tool_controller.observe(loop, r._emit)
        context = r.tool_controller.result_context(loop)["demo__calculate"]
        assert context["available"] is True
        assert [c["status"] for c in context["calls"]] == ["ok", "unavailable"]
        assert context["calls"][0]["result"] == {"value": 86}
        assert all("username" not in c["arguments"] for c in context["calls"])
    finally:
        await r.tool_controller.close()
