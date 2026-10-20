"""DeepSeek replies and bounded goal normalization; role models and embeddings stay local."""

from __future__ import annotations

import json
import time
from urllib.parse import urlsplit

import httpx

from .telemetry import current_run


class DeepSeekUnavailable(RuntimeError):
    def __init__(self, status_code):
        super().__init__(f"DeepSeek unavailable (HTTP {status_code})")
        self.status_code = status_code


class DeepSeekClient:
    def __init__(self, settings, *, custom=None):
        if custom is None and not settings.deepseek_api_key:
            raise ValueError("DEEPSEEK_API_KEY is required")
        self.compatible = custom is not None
        self.thinking_control = custom is None or urlsplit(custom.base_url).hostname == "api.deepseek.com"
        self.max_tokens = 2400
        self.model = custom.model if custom else settings.deepseek_model
        transport = None
        if custom:
            from .user_generation import PublicEndpointTransport
            transport = PublicEndpointTransport()
        self.client = httpx.AsyncClient(
            base_url=(custom.base_url if custom else settings.deepseek_base_url).rstrip("/") + "/",
            headers={"Authorization": f"Bearer {custom.api_key if custom else settings.deepseek_api_key}"},
            timeout=httpx.Timeout(120, connect=10),
            trust_env=False,
            follow_redirects=False,
            transport=transport,
        )

    async def close(self):
        await self.client.aclose()

    def body(self, messages, temperature, stream=False, structured=False):
        body = {
            "model": self.model,
            "messages": messages,
            "stream": stream,
            "thinking": {"type": "disabled"},
            "temperature": temperature,
            "max_tokens": self.max_tokens,
        }
        if structured:
            body["response_format"] = {"type": "json_object"}
        if not self.thinking_control:
            body.pop("thinking", None)
        if stream:
            body["stream_options"] = {"include_usage": True}
        return body

    def record(self, usage, purpose, started):
        telemetry = current_run()
        if telemetry is not None:
            telemetry.record_model_call(
                purpose=purpose,
                model=self.model,
                payload={
                    "prompt_eval_count": usage.get("prompt_tokens", 0),
                    "eval_count": usage.get("completion_tokens", 0),
                    "total_duration": (time.perf_counter() - started) * 1_000_000_000,
                },
            )

    async def chat(self, messages, *, temperature=0.3, purpose="chat", structured=False):
        started = time.perf_counter()
        response = await self.client.post(
            "chat/completions", json=self.body(messages, temperature, structured=structured)
        )
        if not response.is_success:
            raise DeepSeekUnavailable(response.status_code)
        result = response.json()
        self.record(result.get("usage") or {}, purpose, started)
        choice = result["choices"][0]
        if choice.get("finish_reason") != "stop":
            raise RuntimeError("DeepSeek returned an incomplete response")
        content = (choice["message"].get("content") or "").strip()
        if not content:
            raise RuntimeError("DeepSeek returned an empty response")
        return content

    async def chat_json(self, messages, *, temperature=0.0, purpose="structured"):
        result = json.loads(
            await self.chat(messages, temperature=temperature, purpose=purpose, structured=True)
        )
        if not isinstance(result, dict):
            raise TypeError("DeepSeek structured output must be an object")
        return result

    async def stream_chat(self, messages, *, temperature=0.3, purpose="response"):
        started = time.perf_counter()
        usage, completed = {}, False
        try:
            async with self.client.stream(
                "POST", "chat/completions", json=self.body(messages, temperature, stream=True)
            ) as response:
                if not response.is_success:
                    raise DeepSeekUnavailable(response.status_code)
                async for line in response.aiter_lines():
                    if not line.startswith("data:"):
                        continue
                    raw = line[5:].strip()
                    if raw == "[DONE]":
                        break
                    payload = json.loads(raw)
                    if payload.get("usage"):
                        usage = payload["usage"]
                    for choice in payload.get("choices", []):
                        token = choice.get("delta", {}).get("content")
                        if token:
                            yield token
                        if choice.get("finish_reason") == "stop":
                            completed = True
            if not completed:
                raise RuntimeError("DeepSeek stream ended before completion")
        finally:
            self.record(usage, purpose, started)
