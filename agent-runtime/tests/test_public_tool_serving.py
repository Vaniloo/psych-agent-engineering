import json
import os
import subprocess
import sys
from unittest.mock import AsyncMock

import httpx
import pytest

from psych_agent_runtime.config import Settings
from psych_agent_runtime.prefilter import ToolPrefilter, candidate_row
from psych_agent_runtime.public_tool_contract import CONTRACT, TEXT
from psych_agent_runtime.public_tool_service import create_app, validate_row
from psych_agent_runtime.tools import ALL_TOOLS, RECALL_MEMORY, SEARCH_KNOWLEDGE, SUPPORT_RESOURCES
from training.lora.common import TEXT as TRAIN_TEXT


def settings(**kwargs):
    return Settings(executor_provider="public_qwen", executor_base_url="http://new-worker",
                    executor_adapter_sha256="frozen", **kwargs)


TOOLS = [SEARCH_KNOWLEDGE, RECALL_MEMORY]
HISTORY = [{"role": "user", "content": "这周准备考试"}]


def row():
    return candidate_row(HISTORY, "有哪些应对方法", [], TOOLS)


def scorer(scores, **overrides):
    labels = [t.name for t in TOOLS] + ["assistant_text"]

    def respond(request):
        assert request.url.path == "/v1/tool-scores"
        assert json.loads(request.content)["row"] == row()
        return httpx.Response(200, json={"contractVersion": CONTRACT, "candidateIds": labels,
                                         "scores": scores, "adapterSha256": "frozen", "model": "m",
                                         **overrides})
    return respond


async def select(respond, required=()):
    prefilter = ToolPrefilter(settings())
    await prefilter.client.aclose()
    prefilter.client = httpx.AsyncClient(base_url="http://new-worker", transport=httpx.MockTransport(respond))
    try:
        return await prefilter.select(TOOLS, history=HISTORY, request="有哪些应对方法",
                                      observations=[], required=set(required))
    finally:
        await prefilter.close()


def test_candidates_match_training_format_and_hide_identity():
    assert TEXT == TRAIN_TEXT
    r = row()
    assert r["context"][0]["content"] == "这周准备考试"
    assert validate_row(r) == ["search_knowledge", "recall_memory", "assistant_text"]
    for tool in ALL_TOOLS.values():
        props = tool.parameters().get("properties", {})
        assert not {"username", "session_id", "request_id"} & set(props)


@pytest.mark.asyncio
async def test_only_tools_scoring_above_reply_now_are_offered():
    result = await select(scorer([3.0, -1.0, 0.5]))
    assert [t.name for t in result.offered] == ["search_knowledge"]
    assert result.provider == "public_qwen" and result.scores["assistant_text"] == 0.5
    result = await select(scorer([-2.0, -1.0, 0.5]))
    assert result.offered == []  # the router says: answer directly


@pytest.mark.asyncio
async def test_required_tools_survive_a_low_score():
    global TOOLS
    original, TOOLS = TOOLS, [SEARCH_KNOWLEDGE, SUPPORT_RESOURCES]
    try:
        result = await select(scorer([-2.0, -3.0, 0.5]), required={"get_support_resources"})
        assert [t.name for t in result.offered] == ["get_support_resources"]
    finally:
        TOOLS = original


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["busy", "offline", "alignment", "artifact", "nonfinite"])
async def test_scorer_failure_offers_every_tool(failure):
    def respond(request):
        if failure == "busy":
            return httpx.Response(429)
        if failure == "offline":
            raise httpx.ConnectError("offline")
        labels = ["search_knowledge", "recall_memory", "assistant_text"]
        body = {"contractVersion": CONTRACT, "candidateIds": labels, "scores": [3, 1, 0],
                "adapterSha256": "frozen", "model": "m"}
        if failure == "alignment":
            body["candidateIds"] = list(reversed(labels))
        if failure == "artifact":
            body["adapterSha256"] = "wrong"
        if failure == "nonfinite":
            return httpx.Response(200, content=json.dumps({**body, "scores": [float("nan"), 0, 1]}))
        return httpx.Response(200, json=body)
    result = await select(respond)
    assert result.provider == "fallback" and result.offered == TOOLS


@pytest.mark.asyncio
async def test_disabled_prefilter_offers_every_tool_without_network():
    prefilter = ToolPrefilter(Settings(executor_provider="rules"))
    result = await prefilter.select(TOOLS, history=[], request="hi", observations=[], required=set())
    assert result.provider == "disabled" and result.offered == TOOLS and prefilter.client is None


@pytest.mark.asyncio
async def test_worker_auth_contract_and_malformed_data_fail_before_gpu_execution(tmp_path, monkeypatch):
    from psych_agent_runtime.public_tool_contract import specification
    report = {"status": "complete", "contract": CONTRACT, "baseRevision": "base",
              "rendererSpec": specification(), "adapterSha256": "frozen"}
    (tmp_path / "training-report.json").write_text(json.dumps(report))
    monkeypatch.setenv("PUBLIC_TOOL_API_KEY", "test-key")
    app = create_app(tmp_path / "base", tmp_path)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        payload = {"contractVersion": CONTRACT, "row": row()}
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
assert not hasattr(s, "web_filter_url") and not hasattr(s, "memory_provider")
"""], env=env, check=True)
