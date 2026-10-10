package com.vanilo.psych.agent.dto;

import lombok.AllArgsConstructor;
import lombok.Data;
import lombok.NoArgsConstructor;

import java.util.HashMap;
import java.util.Map;

@Data
@NoArgsConstructor
@AllArgsConstructor
public class AgentTurnPersistRequest {
    private String requestId;
    private String username;
    private Long sessionId;
    private String userMessage;
    private String assistantReply;
    private String sessionSummary;
    private String mediumTermSummary;
    private String longTermMemory;
    private Map<String, String> profilePatch = new HashMap<>();

    public AgentTurnPersistRequest(
            String username,
            Long sessionId,
            String userMessage,
            String assistantReply,
            String sessionSummary,
            String mediumTermSummary,
            String longTermMemory,
            Map<String, String> profilePatch) {
        this(
                java.util.UUID.randomUUID().toString(),
                username,
                sessionId,
                userMessage,
                assistantReply,
                sessionSummary,
                mediumTermSummary,
                longTermMemory,
                profilePatch);
    }
}
