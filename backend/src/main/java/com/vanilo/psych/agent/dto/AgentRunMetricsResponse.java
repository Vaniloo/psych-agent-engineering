package com.vanilo.psych.agent.dto;

import lombok.AllArgsConstructor;
import lombok.Data;
import lombok.NoArgsConstructor;

import java.util.List;
import java.util.Map;

@Data
@AllArgsConstructor
@NoArgsConstructor
public class AgentRunMetricsResponse {
    private String requestId;
    private String model;
    private String startedAt;
    private String status;
    private Double totalDurationMs;
    private Double ttftMs;
    private Long promptTokens;
    private Long completionTokens;
    private Long totalTokens;
    private Integer modelCallCount;
    private List<Map<String, Object>> modelCalls;
    private Double tokensPerSecond;
    private Double actualCostUsd;
    private Double estimatedCostUsd;
    private String costMode;
    private Map<String, Double> nodeDurationsMs;
    private Map<String, Object> retrieval;
    private Double mcpDurationMs;
    private Integer fallbackCount;
    private String persistenceStatus;
}
