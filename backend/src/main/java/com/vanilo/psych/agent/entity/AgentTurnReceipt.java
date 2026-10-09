package com.vanilo.psych.agent.entity;

import jakarta.persistence.*;

import lombok.Data;
import lombok.NoArgsConstructor;

import java.time.LocalDateTime;

@Entity
@Table(
        name = "agent_turn_receipts",
        uniqueConstraints = @UniqueConstraint(columnNames = {"user_id", "request_id"}))
@Data
@NoArgsConstructor
public class AgentTurnReceipt {
    @Id
    @GeneratedValue(strategy = GenerationType.IDENTITY)
    private Long id;

    @ManyToOne(optional = false)
    @JoinColumn(name = "user_id")
    private User user;

    @Column(name = "request_id", nullable = false, length = 128)
    private String requestId;

    @Column(nullable = false, length = 64)
    private String fingerprint;

    @Column(nullable = false)
    private Long sessionId;

    @Column(columnDefinition = "TEXT", nullable = false)
    private String userMessage;

    @Column(columnDefinition = "MEDIUMTEXT", nullable = false)
    private String assistantReply;

    @Column(nullable = false)
    private String memoryStatus = "PENDING";

    private Long createdGoalId;

    @Column(nullable = false)
    private LocalDateTime createdAt;
}
