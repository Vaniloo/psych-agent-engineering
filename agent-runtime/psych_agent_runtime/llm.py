"""OpenAI-compatible chat client with native tool calling and streaming.

DeepSeek and Ollama (`/v1`) speak the same Chat Completions protocol, so one
client serves both. The agent loop only depends on `stream_step`, which streams
visible text and returns any tool calls the model requested.
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from dataclasses import dataclass, field

import httpx

from .config import Settings
from .telemetry import current_run


class ModelUnavailable(RuntimeError):
    pass


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: str  # raw JSON text exactly as the model produced it

    def as_message(self) -> dict:
        return {"id": self.id, "type": "function",
                "function": {"name": self.name, "arguments": self.arguments}}


@dataclass
class StepResult:
    content: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    finish_reason: str = ""


class ChatModel:
    def __init__(self, *, base_url: str, api_key: str, model: str, deepseek: bool,
                 timeout: float = 120, max_tokens: int = 2400):
        self.model = model
        self.deepseek = deepseek
        self.max_tokens = max_tokens
        self.client = httpx.AsyncClient(
            base_url=base_url.rstrip("/") + "/",
            headers={"Authorization": f"Bearer {api_key}"} if api_key else {},
            timeout=httpx.Timeout(timeout, connect=10),
            trust_env=False,
            follow_redirects=False,
        )

    @classmethod
    def from_settings(cls, settings: Settings) -> ChatModel:
        if settings.ai_provider == "deepseek":
            if not settings.deepseek_api_key:
                raise ValueError("DEEPSEEK_API_KEY is required when AI_PROVIDER=deepseek")
            return cls(base_url=settings.deepseek_base_url, api_key=settings.deepseek_api_key,
                       model=settings.deepseek_model, deepseek=True)
        return cls(base_url=settings.ollama_base_url.rstrip("/") + "/v1", api_key="",
                   model=settings.chat_model, deepseek=False,
                   timeout=settings.ollama_timeout_seconds)

    async def close(self):
        await self.client.aclose()

    def _body(self, messages, *, tools=None, stream=False, json_mode=False, temperature=0.3):
        body = {"model": self.model, "messages": messages, "stream": stream,
                "temperature": temperature, "max_tokens": self.max_tokens}
        if self.deepseek:
            body["thinking"] = {"type": "disabled"}
        if tools:
            body["tools"] = tools
            body["tool_choice"] = "auto"
        if json_mode:
            body["response_format"] = {"type": "json_object"}
        if stream:
            body["stream_options"] = {"include_usage": True}
        return body

    def _record(self, usage, purpose, started):
        telemetry = current_run()
        if telemetry is not None:
            telemetry.record_model_call(purpose=purpose, model=self.model, payload={
                "prompt_eval_count": usage.get("prompt_tokens", 0),
                "eval_count": usage.get("completion_tokens", 0),
                "total_duration": (time.perf_counter() - started) * 1_000_000_000,
            })

    async def chat_json(self, messages, *, purpose: str) -> dict:
        """One structured classification call (used by the risk review)."""
        started = time.perf_counter()
        response = await self.client.post(
            "chat/completions", json=self._body(messages, json_mode=True, temperature=0))
        if not response.is_success:
            raise ModelUnavailable(f"HTTP {response.status_code}")
        payload = response.json()
        self._record(payload.get("usage") or {}, purpose, started)
        result = json.loads(payload["choices"][0]["message"].get("content") or "{}")
        if not isinstance(result, dict):
            raise TypeError("structured output must be an object")
        return result

    async def stream_step(self, messages, tools, on_text: Callable[[str], None], *,
                          purpose: str = "agent_step") -> StepResult:
        """Stream one assistant turn. Text goes to `on_text`; tool calls are returned."""
        started = time.perf_counter()
        usage: dict = {}
        result = StepResult()
        parts: dict[int, dict] = {}
        try:
            async with self.client.stream(
                "POST", "chat/completions", json=self._body(messages, tools=tools, stream=True)
            ) as response:
                if not response.is_success:
                    raise ModelUnavailable(f"HTTP {response.status_code}")
                async for line in response.aiter_lines():
                    if not line.startswith("data:"):
                        continue
                    raw = line[5:].strip()
                    if raw == "[DONE]":
                        break
                    chunk = json.loads(raw)
                    usage = chunk.get("usage") or usage
                    for choice in chunk.get("choices", []):
                        delta = choice.get("delta") or {}
                        if text := delta.get("content"):
                            result.content += text
                            on_text(text)
                        # Tool call arguments arrive as fragments keyed by index.
                        for item in delta.get("tool_calls") or []:
                            slot = parts.setdefault(item.get("index", 0),
                                                    {"id": "", "name": "", "arguments": ""})
                            slot["id"] = item.get("id") or slot["id"]
                            function = item.get("function") or {}
                            slot["name"] += function.get("name") or ""
                            slot["arguments"] += function.get("arguments") or ""
                        result.finish_reason = choice.get("finish_reason") or result.finish_reason
        finally:
            self._record(usage, purpose, started)
        result.tool_calls = [
            ToolCall(id=p["id"] or f"call_{i}", name=p["name"], arguments=p["arguments"] or "{}")
            for i, p in sorted(parts.items())
        ]
        if not result.finish_reason:
            raise ModelUnavailable("stream ended before the model finished")
        return result
