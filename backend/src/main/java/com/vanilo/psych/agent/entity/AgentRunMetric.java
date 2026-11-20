package com.vanilo.psych.agent.entity;

import lombok.AllArgsConstructor;
import lombok.Data;
import lombok.NoArgsConstructor;

import java.time.LocalDateTime;

@Data
@AllArgsConstructor
@NoArgsConstructor
public class AgentRunMetric {
    private Long id;
    private String requestId;
    private String username;
    private Long sessionId;
    private String model;
    private String status;
    private Double totalDurationMs;
    private Double ttftMs;
    private Long promptTokens;
    private Long completionTokens;
    private Long totalTokens;
    private Double tokensPerSecond;
    private Double actualCostUsd;
    private Double estimatedCostUsd;
    private String costMode;
    private Double mcpDurationMs;
    private Integer fallbackCount;
    private String persistenceStatus;
    private String nodeDurationsJson;
    private String retrievalJson;
    private String modelCallsJson;
    private String pipelineJson;
    private String capabilityCallsJson;
    private LocalDateTime startedAt;
    private LocalDateTime createdAt;
}
