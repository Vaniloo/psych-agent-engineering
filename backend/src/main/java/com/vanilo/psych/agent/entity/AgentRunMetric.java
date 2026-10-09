package com.vanilo.psych.agent.entity;

import jakarta.persistence.*;
import lombok.AllArgsConstructor;
import lombok.Data;
import lombok.NoArgsConstructor;

import java.time.LocalDateTime;

@Entity
@Table(
        name = "agent_run_metrics",
        indexes = {
                @Index(name = "idx_agent_metric_user_started", columnList = "username,started_at"),
                @Index(name = "idx_agent_metric_session", columnList = "session_id")
        },
        uniqueConstraints = @UniqueConstraint(
                name = "uk_agent_metric_request",
                columnNames = "request_id"
        )
)
@Data
@AllArgsConstructor
@NoArgsConstructor
public class AgentRunMetric {
    @Id
    @GeneratedValue(strategy = GenerationType.IDENTITY)
    private Long id;

    @Column(name = "request_id", nullable = false, length = 64)
    private String requestId;

    @Column(nullable = false, length = 64)
    private String username;

    @Column(name = "session_id", nullable = false)
    private Long sessionId;

    @Column(nullable = false, length = 80)
    private String model;

    @Column(nullable = false, length = 24)
    private String status;

    @Column(nullable = false)
    private Double totalDurationMs;

    private Double ttftMs;

    @Column(nullable = false)
    private Long promptTokens;

    @Column(nullable = false)
    private Long completionTokens;

    @Column(nullable = false)
    private Long totalTokens;

    @Column(nullable = false)
    private Double tokensPerSecond;

    @Column(nullable = false)
    private Double actualCostUsd;

    @Column(nullable = false)
    private Double estimatedCostUsd;

    @Column(nullable = false, length = 24)
    private String costMode;

    @Column(nullable = false)
    private Double mcpDurationMs;

    @Column(nullable = false)
    private Integer fallbackCount;

    @Column(nullable = false, length = 24)
    private String persistenceStatus;

    @Lob
    @Column(columnDefinition = "LONGTEXT")
    private String nodeDurationsJson;

    @Lob
    @Column(columnDefinition = "LONGTEXT")
    private String retrievalJson;

    @Lob
    @Column(columnDefinition = "LONGTEXT")
    private String modelCallsJson;

    @Lob @Column(columnDefinition="LONGTEXT")
    private String pipelineJson;
    @Lob @Column(columnDefinition="LONGTEXT")
    private String capabilityCallsJson;

    @Column(name = "started_at", nullable = false)
    private LocalDateTime startedAt;

    @Column(nullable = false)
    private LocalDateTime createdAt;
}
