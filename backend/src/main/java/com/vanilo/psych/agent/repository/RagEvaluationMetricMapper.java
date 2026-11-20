package com.vanilo.psych.agent.repository;

import com.vanilo.psych.agent.entity.RagEvaluationMetric;
import java.util.Optional;
import org.apache.ibatis.annotations.*;

@Mapper
public interface RagEvaluationMetricMapper {
    @Select("SELECT * FROM rag_evaluation_metrics ORDER BY evaluated_at DESC, id DESC LIMIT 1")
    Optional<RagEvaluationMetric> latest();

    @Insert("""
            INSERT INTO rag_evaluation_metrics
                (dataset_version, sample_count, recall_at1, recall_at3, recall_at5, precision_at5,
                 hit_rate_at5, mrr, n_dcg_at5, avg_latency_ms, p95latency_ms, cache_hit_rate, cases_json,
                 evaluated_at, created_at)
            VALUES
                (#{datasetVersion}, #{sampleCount}, #{recallAt1}, #{recallAt3}, #{recallAt5},
                 #{precisionAt5}, #{hitRateAt5}, #{mrr}, #{nDcgAt5}, #{avgLatencyMs}, #{p95LatencyMs},
                 #{cacheHitRate}, #{casesJson}, #{evaluatedAt}, #{createdAt})
            """)
    @Options(useGeneratedKeys = true, keyProperty = "id")
    void insert(RagEvaluationMetric row);
}
