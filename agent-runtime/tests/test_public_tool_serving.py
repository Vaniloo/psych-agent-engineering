import json
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest

from benchmarks.loop_probe import run_controller
from benchmarks.public_tool_replay import CATALOG
from psych_agent_runtime.config import Settings
from psych_agent_runtime.public_tool_client import CONTRACT, TEXT, PublicToolDecisions, candidate_row
from psych_agent_runtime.public_tool_service import create_app, validate_row
from psych_agent_runtime.route_policy import plan_for_route
from psych_agent_runtime.tool_controller import ToolController
from training.public_tools.course import TEXT as TRAIN_TEXT


def settings(**kwargs):
    return Settings(executor_provider="public_qwen", executor_base_url="http://new-worker",
                    executor_goal_provider="rules", executor_adapter_sha256="frozen", **kwargs)


def state():
    return {"request": "有哪些应对方法", "allowedTools": ["search_knowledge", "memory_search"],
            "observations": {}, "history": [{"role": "user", "content": "这周准备考试"}]}


def test_catalog_matches_real_registry_and_text_matches_training_without_identity_parameters():
    row = candidate_row(state())
    assert TEXT == TRAIN_TEXT
    assert row["context"][0]["content"] == "这周准备考试"
    for item in row["candidateTools"][:-1]:
        assert item["tool"] == CATALOG[item["tool"]["name"]]
    assert validate_row(row) == ["search_knowledge", "memory_search", "assistant_text"]
    with pytest.raises(ValueError, match="registered reads"):
        candidate_row({**state(), "allowedTools": ["create_goal"]})


@pytest.mark.asyncio
async def test_text_maps_only_to_read_handoff_and_goal_tasks_never_reach_the_worker():
    client = PublicToolDecisions(settings())

    def respond(request):
        assert request.url.path == "/v1/tool-scores"
        assert json.loads(request.content)["row"] == candidate_row(state())
        return httpx.Response(200, json={"contractVersion": CONTRACT,
            "candidateIds": ["search_knowledge", "memory_search", "assistant_text"],
            "scores": [-8, -10, -2], "adapterSha256": "frozen", "model": "new"})

    await client.client.aclose()
    client.client = httpx.AsyncClient(base_url="http://new-worker", transport=httpx.MockTransport(respond))
    try:
        answer = await client.evaluate(state(), ["next_action"])
        assert answer["decisions"]["next_action"]["choice"] == "finish"
        assert answer["decisions"]["next_action"]["calibrated"] is False
        with pytest.raises(ValueError, match="goal"):
            await client.evaluate(state(), ["goal_outcome"])
    finally:
        await client.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["busy", "offline", "alignment", "artifact", "nonfinite"])
async def test_failure_uses_existing_finite_choice_worker_instead_of_inventing_an_action(failure):
    client = PublicToolDecisions(settings(executor_fallback_base_url="http://old-worker"))
    fallback = AsyncMock(return_value={"provider": "laya", "decisions": {
        "next_action": {"choice": "search_knowledge", "accepted": True}}})
    client.fallback.evaluate = fallback

    def respond(request):
        if failure == "busy":
            return httpx.Response(429)
        if failure == "offline":
            raise httpx.ConnectError("offline")
        labels = ["search_knowledge", "memory_search", "assistant_text"]
        result = {"contractVersion": CONTRACT, "candidateIds": labels, "scores": [3, 1, 0],
                  "adapterSha256": "frozen", "model": "new"}
        if failure == "alignment": result["candidateIds"] = list(reversed(labels))
        if failure == "artifact": result["adapterSha256"] = "wrong"
        if failure == "nonfinite":
            return httpx.Response(200, content=json.dumps({**result, "scores": [float("nan"), 0, 1]}))
        return httpx.Response(200, json=result)

    await client.client.aclose()
    client.client = httpx.AsyncClient(base_url="http://new-worker", transport=httpx.MockTransport(respond))
    try:
        result = await client.evaluate(state(), ["next_action"])
        assert result["provider"] == "laya"
        assert result["fallbackReason"] == "public_tool_unavailable_or_invalid"
        fallback.assert_awaited_once()
        assert set(fallback.call_args.kwargs["labels_by_task"]["next_action"]) == {
            "search_knowledge", "memory_search", "finish", "clarify"}
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_required_reads_precede_new_model_and_goals_keep_their_separate_worker():
    controller = ToolController(replace(settings(), executor_goal_provider="qwen",
                                         executor_goal_base_url="http://goal-worker"))
    controller.model.evaluate = AsyncMock(return_value={"provider": "public_qwen", "decisions": {
        "next_action": {"choice": "finish", "accepted": True}}})
    controller.goal_model.evaluate = AsyncMock(side_effect=AssertionError("read reached goal worker"))
    try:
        plan = plan_for_route("memory", "我喜欢什么", "test")
        invoke = AsyncMock(return_value={"evidence": []})
        await run_controller(controller, plan, "我喜欢什么", invoke, lambda *_: None)
        assert invoke.call_args_list[0].args[0] == "memory_search"
        controller.goal_model.evaluate.assert_not_called()
        assert controller.goal_model is not controller.model
    finally:
        await controller.close()
    with pytest.raises(ValueError, match="separate"):
        ToolController(replace(settings(), executor_goal_provider=""))


@pytest.mark.asyncio
async def test_worker_auth_contract_and_malformed_data_fail_before_gpu_execution(tmp_path, monkeypatch):
    from training.public_tools.contract import specification
    report = {"status": "complete", "contract": CONTRACT, "baseRevision": "base",
              "rendererSpec": specification(), "adapterSha256": "frozen"}
    (tmp_path / "training-report.json").write_text(json.dumps(report))
    monkeypatch.setenv("PUBLIC_TOOL_API_KEY", "test-key")
    app = create_app(tmp_path / "base", tmp_path)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        payload = {"contractVersion": CONTRACT, "row": candidate_row(state())}
        assert (await client.post("/v1/tool-scores", json=payload)).status_code == 401
        headers = {"Authorization": "Bearer test-key"}
        assert (await client.post("/v1/tool-scores", headers=headers,
                                 json={**payload, "contractVersion": "psych-executor-v2"})).status_code == 409
        assert (await client.post("/v1/tool-scores", headers=headers,
                                 json={**payload, "row": {}})).status_code == 422


@pytest.mark.asyncio
async def test_manifest_uses_public_contract_only_for_read_model(monkeypatch):
    from psych_agent_runtime import api
    monkeypatch.setattr(api, "settings", replace(settings(), executor_goal_provider="qwen",
        executor_goal_base_url="http://goal-worker", router_provider="rules", memory_provider="rules"))
    client = AsyncMock(); client.__aenter__.return_value = client

    async def get(url):
        public = url.startswith("http://new-worker")
        return httpx.Response(200, request=httpx.Request("GET", url), json={
            "profile": "executor", "ready": True, "tasks": {"next_action": True, "goal_creation": True},
            "contractVersion": CONTRACT if public else "psych-executor-v2",
            "artifact": {"adapterSha256": "frozen"}, "model": "new" if public else "old-goal"})

    client.get.side_effect = get
    monkeypatch.setattr(api.httpx, "AsyncClient", lambda **kwargs: client)
    info = (await api.runtime_info())["profiles"][1]
    assert info["status"] == "ready" and info["contract"] == CONTRACT
    assert info["tasks"] == info["enabledTasks"] == ["next_action"]
    assert info["goalDecisions"]["contract"] == "psych-executor-v2"
