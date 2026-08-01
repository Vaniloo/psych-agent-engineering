from __future__ import annotations

import json
import logging

from .config import Settings
from .ollama_client import OllamaClient
from .schemas import AgentContext, MemoryCompression
from .token_budget import TokenEstimator

log = logging.getLogger(__name__)


class MemoryCompressor:
    def __init__(
        self,
        settings: Settings,
        ollama: OllamaClient,
        estimator: TokenEstimator | None = None,
    ) -> None:
        self.settings = settings
        self.ollama = ollama
        self.estimator = estimator or TokenEstimator()

    def should_compress(self, context: AgentContext, user_message: str, reply: str) -> bool:
        history_tokens = sum(self.estimator.count(item.content) for item in context.messages)
        projected = history_tokens + self.estimator.count(user_message) + self.estimator.count(reply)
        return projected >= self.settings.memory_compression_trigger_tokens

    async def compress(
        self,
        context: AgentContext,
        user_message: str,
        reply: str,
    ) -> MemoryCompression:
        if not self.should_compress(context, user_message, reply):
            return MemoryCompression(compressed=False)

        recent = [
            {"role": item.role, "content": item.content}
            for item in context.messages[-20:]
        ]
        payload = {
            "oldSessionSummary": context.session_summary,
            "oldMediumTermSummary": context.medium_term_summary,
            "oldLongTermMemory": context.long_term_memory,
            "profile": context.profile.model_dump(),
            "recentMessages": recent,
            "latestTurn": {"user": user_message, "assistant": reply},
        }
        try:
            result = await self.ollama.chat_json(
                [
                    {
                        "role": "system",
                        "content": (
                            "你是心理支持系统的记忆压缩器。只保留对后续支持有用、相对稳定、"
                            "由对话直接支持的信息；不要做医学诊断，不要夸大风险。返回 JSON："
                            '{"session_summary":"","medium_term_summary":"","long_term_memory":"",'
                            '"profile_patch":{"profile_summary":"","concerns":"","preferences":"",'
                            '"coping_strategies":"","risk_signals":"","support_goals":""}}。'
                            "每个字段最多160字。"
                        ),
                    },
                    {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
                ],
                temperature=0.0,
                purpose="memory_compression",
            )
            return MemoryCompression(
                session_summary=self._limit(result.get("session_summary"), 1000),
                medium_term_summary=self._limit(result.get("medium_term_summary"), 1000),
                long_term_memory=self._limit(result.get("long_term_memory"), 1000),
                profile_patch={
                    key: self._limit(value, 1000)
                    for key, value in (result.get("profile_patch") or {}).items()
                    if isinstance(value, str) and value.strip()
                },
                compressed=True,
            )
        except Exception:
            log.warning("Model memory compression failed; using deterministic summary", exc_info=True)
            fallback = f"{context.session_summary}\n本轮用户：{user_message}".strip()
            return MemoryCompression(
                session_summary=self._limit(fallback, 1000),
                compressed=True,
            )

    @staticmethod
    def _limit(value: object, length: int) -> str:
        text = "" if value is None else str(value).strip()
        return text[:length]
