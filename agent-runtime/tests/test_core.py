import unittest
from types import SimpleNamespace

from psych_agent_runtime.bm25 import BM25, tokenize_for_search
from psych_agent_runtime.chunking import TokenAwareChunker
from psych_agent_runtime.config import Settings
from psych_agent_runtime.evaluation import RagEvaluator
from psych_agent_runtime.rag import RagEngine
from psych_agent_runtime.safety import SafetyScanner
from psych_agent_runtime.schemas import RagHit
from psych_agent_runtime.telemetry import RunTelemetry
from psych_agent_runtime.token_budget import ContextBudgetManager, TokenEstimator


class SafetyScannerTests(unittest.TestCase):
    def test_explicit_crisis_is_high_risk(self):
        result = SafetyScanner().scan("我已经准备今晚结束生命")
        self.assertTrue(result.high_risk)
        self.assertEqual(result.action, "trigger_crisis_workflow")

    def test_negated_statement_is_not_automatically_escalated(self):
        result = SafetyScanner().scan("我没有自杀想法，只是最近压力很大")
        self.assertFalse(result.high_risk)

    def test_current_denial_is_not_overturned_by_now(self):
        for text in ["我现在安全，不再有伤害自己的想法。", "我现在不会自杀，只想聊聊压力。"]:
            self.assertFalse(SafetyScanner().scan(text).high_risk)

    def test_denial_does_not_suppress_other_danger_or_preparation(self):
        for text in ["我没有自杀想法，但我想割腕。", "我不想死，但已经准备好了遗书。"]:
            self.assertTrue(SafetyScanner().scan(text).high_risk)


class ChunkingTests(unittest.TestCase):
    def test_chunking_is_bounded_and_overlapped(self):
        text = "焦虑时先缓慢呼吸。" * 200
        chunks = TokenAwareChunker().split(text, chunk_size=80, overlap=15)
        self.assertGreater(len(chunks), 2)
        self.assertTrue(all(chunk.estimated_tokens <= 95 for chunk in chunks))
        self.assertTrue(set(chunks[0].text[-10:]) & set(chunks[1].text[:20]))


class BM25Tests(unittest.TestCase):
    def test_chinese_bigrams_are_searchable(self):
        tokens = tokenize_for_search("考试焦虑怎么缓解")
        self.assertIn("焦虑", tokens)
        index = BM25(["睡眠卫生建议", "考试焦虑可以先拆分任务"])
        result = index.search("考试焦虑", 2)
        self.assertEqual(result[0].index, 1)

    def test_domain_query_expansion_and_category_hint(self):
        expanded = RagEngine._expand_query("请给我改善睡眠的建议")
        self.assertIn("失眠", expanded)
        self.assertEqual(
            RagEngine._infer_categories("请给我改善睡眠的建议"),
            {"sleep"},
        )


class ContextBudgetTests(unittest.TestCase):
    def test_newest_messages_are_kept_under_budget(self):
        context = SimpleNamespace(
            username="alice",
            session_id=1,
            session_summary="",
            medium_term_summary="",
            long_term_memory="",
            role_prompt="",
            profile=SimpleNamespace(
                profile_summary="",
                concerns="",
                preferences="",
                coping_strategies="",
                risk_signals="",
            ),
            messages=[
                SimpleNamespace(role="user", content=f"第{i}轮 " + "压力" * 80)
                for i in range(20)
            ],
        )
        hits = [
            SimpleNamespace(
                id="1",
                document_id="d1",
                content="呼吸练习" * 100,
                category="anxiety",
                source="test",
            )
        ]
        manager = ContextBudgetManager(900, 200, TokenEstimator())
        result = manager.build(context, "我该怎么办", hits)
        self.assertLessEqual(result.estimated_tokens, 900)
        self.assertGreater(result.dropped_messages, 0)
        self.assertIn("第19轮", result.history[-1]["content"])

    def test_long_latin_text_is_counted_and_truncated(self):
        estimator = TokenEstimator()
        text = "engineering" * 500
        self.assertGreater(estimator.count(text), 500)
        truncated = estimator.truncate(text, 100)
        self.assertLessEqual(estimator.count(truncated), 100)


class TelemetryTests(unittest.TestCase):
    def test_ollama_usage_and_local_cost_are_reported_honestly(self):
        settings = Settings(
            cost_mode="local",
            input_cost_per_million_tokens=1.0,
            output_cost_per_million_tokens=2.0,
        )
        telemetry = RunTelemetry("request-1", settings)
        telemetry.record_model_call(
            purpose="response",
            model="qwen",
            payload={
                "prompt_eval_count": 120,
                "eval_count": 30,
                "total_duration": 2_000_000_000,
                "eval_duration": 1_000_000_000,
            },
        )
        metrics = telemetry.snapshot()
        self.assertEqual(metrics["totalTokens"], 150)
        self.assertEqual(metrics["tokensPerSecond"], 30.0)
        self.assertEqual(metrics["actualCostUsd"], 0.0)
        self.assertGreater(metrics["estimatedCostUsd"], 0.0)


class RagEvaluationMetricTests(unittest.TestCase):
    def test_relevance_requires_category_and_marker(self):
        hits = [
            RagHit(
                id="1",
                document_id="doc-1",
                content="可以尝试 4-6 呼吸",
                category="anxiety",
                source="test",
            ),
            RagHit(
                id="2",
                document_id="doc-2",
                content="可以尝试 4-6 呼吸",
                category="sleep",
                source="test",
            ),
        ]
        relevance = RagEvaluator._relevance_vector(
            hits,
            [{"category": "anxiety", "contains": ["4-6 呼吸"]}],
        )
        self.assertEqual(relevance, [1, 0])


if __name__ == "__main__":
    unittest.main()
