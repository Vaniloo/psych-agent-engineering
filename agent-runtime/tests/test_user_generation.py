import asyncio
import json
import socket
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from pydantic import ValidationError

from psych_agent_runtime.config import Settings
from psych_agent_runtime.deepseek_client import DeepSeekClient
from psych_agent_runtime.ollama_client import OllamaClient
from psych_agent_runtime.telemetry import end_run, start_run
from psych_agent_runtime.user_generation import (
    ModelApiConfig,
    PublicEndpointTransport,
    active_generation,
    generation_for_user,
)


@pytest.mark.parametrize("url", ["http://example.com", "https://user:secret@example.com/v1",
                                "https://example.com/v1?key=secret", "https://example.com:9000/v1"])
def test_ambiguous_or_non_https_endpoints_rejected(url):
    with pytest.raises(ValidationError):
        ModelApiConfig(baseUrl=url, model="model-a", apiKey="private-example")


@pytest.mark.asyncio
@pytest.mark.parametrize("address", ["127.0.0.1", "10.0.0.1", "169.254.169.254", "::1", "fc00::1", "100.64.0.1"])
async def test_dns_to_private_network_never_receives_credentials(monkeypatch, address):
    transport = PublicEndpointTransport()
    transport.inner.aclose = AsyncMock()
    transport.inner.handle_async_request = AsyncMock()
    monkeypatch.setattr(asyncio.get_running_loop(), "getaddrinfo", AsyncMock(return_value=[
        (socket.AF_INET, socket.SOCK_STREAM, 6, "", (address, 443))]))
    try:
        with pytest.raises(ValueError, match="公开网络"):
            await transport.handle_async_request(httpx.Request("POST", "https://provider.example/v1/chat/completions",
                                                               headers={"Authorization": "Bearer dummy-private"}))
        transport.inner.handle_async_request.assert_not_awaited()
    finally:
        await transport.aclose()


@pytest.mark.asyncio
async def test_validated_destination_is_pinned_with_original_tls_hostname(monkeypatch):
    transport = PublicEndpointTransport()
    transport.inner.handle_async_request = AsyncMock(return_value=httpx.Response(200))
    monkeypatch.setattr(asyncio.get_running_loop(), "getaddrinfo", AsyncMock(return_value=[
        (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("8.8.8.8", 443))]))
    try:
        request = httpx.Request("POST", "https://provider.example/v1/chat/completions")
        await transport.handle_async_request(request)
        assert request.url.host == "8.8.8.8"
        assert request.headers["host"] == request.extensions["sni_hostname"] == "provider.example"
    finally:
        await transport.aclose()


@pytest.mark.asyncio
async def test_concurrent_accounts_keep_separate_clients_and_do_not_expose_keys(monkeypatch):
    import psych_agent_runtime.deepseek_client as module
    original = DeepSeekClient
    seen = []

    def factory(settings, *, custom):
        client = original(settings, custom=custom)
        old = client.client

        def respond(request):
            body = json.loads(request.content)
            assert "thinking" not in body
            seen.append((body["model"], request.headers["authorization"]))
            return httpx.Response(200, json={"choices": [{"finish_reason": "stop", "message": {"content": body["model"]}}]})
        client.client = httpx.AsyncClient(base_url=custom.base_url + "/", headers={"Authorization": "Bearer " + custom.api_key},
                                          transport=httpx.MockTransport(respond))
        client._test_old = old
        return client

    monkeypatch.setattr(module, "DeepSeekClient", factory)
    settings = Settings(ai_provider="ollama")
    shared = OllamaClient(settings)

    async def account(name):
        backend = SimpleNamespace(model_api=AsyncMock(return_value={"enabled": True, "baseUrl": "https://provider.example/v1",
                                                                   "model": name, "apiKey": "dummy-" + name}))
        run, token = start_run(settings, name)
        try:
            async with generation_for_user(backend, name):
                scoped = active_generation.get()
                await asyncio.sleep(0)
                assert await shared.chat([]) == name
                assert run.snapshot()["model"] == name
                assert run.snapshot()["costMode"] == "external_unpriced"
                assert "dummy-" not in json.dumps(run.snapshot())
                await scoped._test_old.aclose()
            assert active_generation.get() is None and shared._generation is None
        finally:
            end_run(token)
    try:
        await asyncio.gather(account("alice-model"), account("bob-model"))
        assert sorted(seen) == [("alice-model", "Bearer dummy-alice-model"), ("bob-model", "Bearer dummy-bob-model")]
    finally:
        await shared.close()


def test_config_dump_and_repr_do_not_contain_secret():
    config = ModelApiConfig(baseUrl="https://provider.example/v1/chat/completions", model="example", apiKey="dummy-private")
    assert config.base_url == "https://provider.example/v1"
    assert "dummy-private" not in repr(config) + config.model_dump_json()
