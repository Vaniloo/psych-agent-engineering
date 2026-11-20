package com.vanilo.psych.agent.entity;

import lombok.Data;
import lombok.NoArgsConstructor;

import java.time.LocalDateTime;

@Data
@NoArgsConstructor
public class AgentTurnReceipt {
    private Long id;
    private User user;
    private String requestId;
    private String fingerprint;
    private Long sessionId;
    private String userMessage;
    private String assistantReply;
    private String memoryStatus = "PENDING";
    private Long createdGoalId;
    private LocalDateTime createdAt;
}
