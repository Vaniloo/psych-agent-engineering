import json
import os
import subprocess
import sys
from dataclasses import replace
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
                    executor_adapter_sha256="frozen", **kwargs)


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
        candidate_row({**state(), "allowedTools": ["persist_turn"]})


@pytest.mark.asyncio
async def test_text_maps_only_to_read_handoff_and_unsupported_tasks_never_reach_the_worker():
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
        with pytest.raises(ValueError, match="next_action"):
            await client.evaluate(state(), ["unsupported"])
    finally:
        await client.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["busy", "offline", "alignment", "artifact", "nonfinite"])
async def test_failure_reaches_controller_for_deterministic_fallback(failure):
    client = PublicToolDecisions(settings())

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
        with pytest.raises((ValueError, httpx.HTTPError)):
            await client.evaluate(state(), ["next_action"])
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_required_reads_precede_executor():
    controller = ToolController(settings())
    controller.model.evaluate = AsyncMock(return_value={"provider": "public_qwen", "decisions": {
        "next_action": {"choice": "finish", "accepted": True}}})
    try:
        plan = plan_for_route("memory", "我喜欢什么", "test")
        invoke = AsyncMock(return_value={"evidence": []})
        await run_controller(controller, plan, "我喜欢什么", invoke, lambda *_: None)
        assert invoke.call_args_list[0].args[0] == "memory_search"
    finally:
        await controller.close()
    with pytest.raises(ValueError, match="EXECUTOR_PROVIDER"):
        ToolController(replace(settings(), executor_provider="laya"))


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
    monkeypatch.setattr(api, "settings", settings())
    client = AsyncMock(); client.__aenter__.return_value = client

    async def get(url):
        return httpx.Response(200, request=httpx.Request("GET", url), json={
            "profile": "executor", "ready": True, "tasks": {"next_action": True},
            "contractVersion": CONTRACT,
            "artifact": {"adapterSha256": "frozen"}, "model": "executor"})

    client.get.side_effect = get
    monkeypatch.setattr(api.httpx, "AsyncClient", lambda **kwargs: client)
    info = (await api.runtime_info())["profiles"][1]
    assert info["status"] == "ready" and info["contract"] == CONTRACT
    assert info["tasks"] == info["enabledTasks"] == ["next_action"]
    client.get.assert_awaited_once_with("http://new-worker/health")

def test_deployed_settings_ignore_retired_worker_configuration():
    # Dataclass defaults read the process environment at import time.
    env = dict(os.environ, ROUTER_PROVIDER="qwen", MEMORY_PROVIDER="qwen",
               EXECUTOR_GOAL_PROVIDER="qwen", GOAL_GATE_MODE="model",
               EXECUTOR_FALLBACK_BASE_URL="http://retired",
               MEMORY_LIFECYCLE_SHADOW_URL="http://retired", WEB_FILTER_URL="http://retired")
    env.pop("EXECUTOR_PROVIDER", None)
    subprocess.run([sys.executable, "-c", """
from psych_agent_runtime.config import get_settings
s = get_settings()
assert s.executor_provider == "public_qwen"
assert not s.web_filter_url
"""], env=env, check=True)
