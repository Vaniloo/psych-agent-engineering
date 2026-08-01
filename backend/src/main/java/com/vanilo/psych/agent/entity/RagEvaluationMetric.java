package com.vanilo.psych.agent.entity;

import jakarta.persistence.*;
import lombok.AllArgsConstructor;
import lombok.Data;
import lombok.NoArgsConstructor;

import java.time.LocalDateTime;

@Entity
@Table(name = "rag_evaluation_metrics")
@Data
@AllArgsConstructor
@NoArgsConstructor
public class RagEvaluationMetric {
    @Id
    @GeneratedValue(strategy = GenerationType.IDENTITY)
    private Long id;

    @Column(nullable = false, length = 80)
    private String datasetVersion;

    @Column(nullable = false)
    private Integer sampleCount;

    @Column(nullable = false)
    private Double recallAt1;

    @Column(nullable = false)
    private Double recallAt3;

    @Column(nullable = false)
    private Double recallAt5;

    @Column(nullable = false)
    private Double precisionAt5;

    @Column(nullable = false)
    private Double hitRateAt5;

    @Column(nullable = false)
    private Double mrr;

    @Column(nullable = false)
    private Double nDcgAt5;

    @Column(nullable = false)
    private Double avgLatencyMs;

    @Column(nullable = false)
    private Double p95LatencyMs;

    @Column(nullable = false)
    private Double cacheHitRate;

    @Lob
    @Column(columnDefinition = "LONGTEXT")
    private String casesJson;

    @Column(nullable = false)
    private LocalDateTime evaluatedAt;

    @Column(nullable = false)
    private LocalDateTime createdAt;
}
