import json

import httpx
import pytest

from psych_agent_runtime.config import Settings
from psych_agent_runtime.deepseek_client import DeepSeekClient
from psych_agent_runtime.ollama_client import OllamaClient
from psych_agent_runtime.telemetry import end_run, start_run


@pytest.mark.asyncio
async def test_cloud_chat_keeps_embedding_local_and_usage_real():
    settings = Settings(ai_provider="deepseek", deepseek_api_key="test-private-key")
    client = OllamaClient(settings)
    remote, local = [], []

    def cloud(request):
        remote.append(json.loads(request.content))
        assert request.headers["authorization"] == "Bearer test-private-key"
        return httpx.Response(
            200,
            json={
                "choices": [{"finish_reason": "stop", "message": {"content": '{"ok":true}'}}],
                "usage": {"prompt_tokens": 11, "completion_tokens": 4},
            },
        )

    def embed(request):
        local.append(request.url.path)
        assert "authorization" not in request.headers
        return httpx.Response(200, json={"embeddings": [[0.1, 0.2]]})

    await client._generation.client.aclose()
    await client._client.aclose()
    client._generation.client = httpx.AsyncClient(
        base_url="https://api.deepseek.com/",
        headers={"Authorization": "Bearer test-private-key"},
        transport=httpx.MockTransport(cloud),
    )
    client._client = httpx.AsyncClient(
        base_url="http://local/", transport=httpx.MockTransport(embed)
    )
    run, token = start_run(settings, "test")
    try:
        assert await client.chat_json([{"role": "user", "content": "return JSON"}]) == {"ok": True}
        assert await client.embed(["hello"]) == [[0.1, 0.2]]
        assert remote[0]["model"] == "deepseek-flash"
        assert remote[0]["thinking"] == {"type": "disabled"}
        assert local == ["/api/embed"]
        snapshot = run.snapshot()
        assert snapshot["model"] == "deepseek-flash"
        assert snapshot["totalTokens"] == 15
        assert "test-private-key" not in json.dumps(snapshot)
    finally:
        end_run(token)
        await client.close()


@pytest.mark.asyncio
async def test_stream_usage_and_incomplete_stream_rejected():
    client = DeepSeekClient(Settings(deepseek_api_key="test"))

    async def invoke(body):
        await client.client.aclose()
        client.client = httpx.AsyncClient(
            base_url="https://api.deepseek.com/",
            transport=httpx.MockTransport(lambda _: httpx.Response(200, text=body)),
        )
        return "".join([t async for t in client.stream_chat([])])

    try:
        assert (
            await invoke(
                'data: {"choices":[{"delta":{"content":"你好"},"finish_reason":null}]}\n\ndata: {"choices":[{"delta":{},"finish_reason":"stop"}]}\n\ndata: {"choices":[],"usage":{"prompt_tokens":2,"completion_tokens":1}}\n\ndata: [DONE]\n\n'
            )
            == "你好"
        )
        with pytest.raises(RuntimeError, match="before completion"):
            await invoke('data: {"choices":[{"delta":{"content":"partial"}}]}\n\ndata: [DONE]\n\n')
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_provider_error_hides_server_body():
    client = DeepSeekClient(Settings(deepseek_api_key="test"))
    await client.client.aclose()
    client.client = httpx.AsyncClient(
        base_url="https://api.deepseek.com/",
        transport=httpx.MockTransport(lambda _: httpx.Response(401, text="sensitive service body")),
    )
    try:
        with pytest.raises(RuntimeError, match=r"HTTP 401") as error:
            await client.chat([])
        assert "sensitive" not in str(error.value)
    finally:
        await client.close()
