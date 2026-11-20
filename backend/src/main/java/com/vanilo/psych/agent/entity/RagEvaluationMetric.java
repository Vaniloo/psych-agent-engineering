package com.vanilo.psych.agent.entity;

import lombok.AllArgsConstructor;
import lombok.Data;
import lombok.NoArgsConstructor;

import java.time.LocalDateTime;

@Data
@AllArgsConstructor
@NoArgsConstructor
public class RagEvaluationMetric {
    private Long id;
    private String datasetVersion;
    private Integer sampleCount;
    private Double recallAt1;
    private Double recallAt3;
    private Double recallAt5;
    private Double precisionAt5;
    private Double hitRateAt5;
    private Double mrr;
    private Double nDcgAt5;
    private Double avgLatencyMs;
    private Double p95LatencyMs;
    private Double cacheHitRate;
    private String casesJson;
    private LocalDateTime evaluatedAt;
    private LocalDateTime createdAt;
}
