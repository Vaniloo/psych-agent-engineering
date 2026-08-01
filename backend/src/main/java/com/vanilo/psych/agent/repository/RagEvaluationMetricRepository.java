package com.vanilo.psych.agent.repository;

import com.vanilo.psych.agent.entity.RagEvaluationMetric;
import org.springframework.data.jpa.repository.JpaRepository;

import java.util.Optional;

public interface RagEvaluationMetricRepository extends JpaRepository<RagEvaluationMetric, Long> {
    Optional<RagEvaluationMetric> findFirstByOrderByEvaluatedAtDesc();
}
