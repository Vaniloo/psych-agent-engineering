from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .schemas import AgentContext, RagHit


class TokenEstimator:
    """Tokenizer-independent estimate suitable for mixed Chinese/English text."""

    _latin_word = re.compile(r"[A-Za-z0-9_]+")
    _cjk = re.compile(r"[\u3400-\u9fff]")

    def count(self, text: str | None) -> int:
        if not text:
            return 0
        latin_matches = self._latin_word.findall(text)
        latin_tokens = sum(max(1, (len(word) + 3) // 4) for word in latin_matches)
        cjk_chars = len(self._cjk.findall(text))
        remaining = max(0, len(text) - cjk_chars)
        punctuation_and_spaces = max(0, remaining - sum(len(x) for x in latin_matches))
        return max(1, latin_tokens + (cjk_chars + 1) // 2 + punctuation_and_spaces // 4)

    def truncate(self, text: str, token_limit: int) -> str:
        if token_limit <= 0:
            return ""
        if self.count(text) <= token_limit:
            return text
        low = 0
        high = len(text)
        while low < high:
            mid = (low + high + 1) // 2
            if self.count(text[:mid]) <= token_limit:
                low = mid
            else:
                high = mid - 1
        return text[:low].rstrip()


@dataclass(frozen=True)
class BudgetedContext:
    system_context: str
    history: list[dict[str, str]]
    citations: list[RagHit]
    estimated_tokens: int
    dropped_messages: int
    compression_recommended: bool


class ContextBudgetManager:
    def __init__(
        self,
        max_tokens: int,
        response_reserve: int,
        estimator: TokenEstimator | None = None,
    ) -> None:
        self.max_tokens = max_tokens
        self.response_reserve = response_reserve
        self.estimator = estimator or TokenEstimator()

    def build(
        self,
        context: AgentContext,
        user_message: str,
        citations: list[RagHit],
        extra_context_tokens: int = 0,
    ) -> BudgetedContext:
        available = max(256, self.max_tokens - self.response_reserve)
        memory_budget = min(1800, max(128, available // 3))
        memory_text = self.estimator.truncate(self._memory_text(context), memory_budget)
        system_tokens = (
            self.estimator.count(memory_text)
            + self.estimator.count(user_message)
            + max(0, extra_context_tokens)
            + 100
        )
        remaining = max(0, available - system_tokens)

        selected_citations: list[RagHit] = []
        citation_tokens = 0
        citation_budget = min(1800, remaining // 3)
        for item in citations:
            item_tokens = self.estimator.count(item.content) + 30
            if citation_tokens + item_tokens > citation_budget:
                break
            selected_citations.append(item)
            citation_tokens += item_tokens

        history_budget = max(0, available - system_tokens - citation_tokens)
        selected_history: list[dict[str, str]] = []
        history_tokens = 0
        for message in reversed(context.messages):
            item_tokens = self.estimator.count(message.content) + 8
            if history_tokens + item_tokens > history_budget:
                if not selected_history and history_budget > 8:
                    selected_history.append(
                        {
                            "role": message.role,
                            "content": self.estimator.truncate(
                                message.content,
                                history_budget - 8,
                            ),
                        }
                    )
                    history_tokens = self.estimator.count(selected_history[0]["content"]) + 8
                break
            selected_history.append({"role": message.role, "content": message.content})
            history_tokens += item_tokens
        selected_history.reverse()

        dropped = max(0, len(context.messages) - len(selected_history))
        total = system_tokens + citation_tokens + history_tokens
        return BudgetedContext(
            system_context=memory_text,
            history=selected_history,
            citations=selected_citations,
            estimated_tokens=total,
            dropped_messages=dropped,
            compression_recommended=dropped > 0 or total > int(available * 0.9),
        )

    def _memory_text(self, context: AgentContext) -> str:
        profile = context.profile
        return "\n".join(
            [
                f"当前会话摘要：{context.session_summary or '暂无'}",
                f"中期摘要：{context.medium_term_summary or '暂无'}",
                f"长期记忆：{context.long_term_memory or '暂无'}",
                f"画像概览：{profile.profile_summary or '暂无'}",
                f"主要困扰：{profile.concerns or '暂无'}",
                f"沟通偏好：{profile.preferences or '暂无'}",
                f"有效应对方式：{profile.coping_strategies or '暂无'}",
                f"风险信号：{profile.risk_signals or '暂无'}",
                f"角色卡：{context.role_prompt or '默认温和风格'}",
            ]
        )

    def debug_summary(self, budget: BudgetedContext) -> dict[str, Any]:
        return {
            "estimatedTokens": budget.estimated_tokens,
            "maxTokens": self.max_tokens,
            "responseReserve": self.response_reserve,
            "historyMessages": len(budget.history),
            "droppedMessages": budget.dropped_messages,
            "citations": len(budget.citations),
            "compressionRecommended": budget.compression_recommended,
        }
