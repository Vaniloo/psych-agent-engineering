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
    private String username;
    private Long sessionId;
    private String userMessage;
    private String assistantReply;
    private String sessionSummary;
    private String mediumTermSummary;
    private String longTermMemory;
    private Map<String, String> profilePatch = new HashMap<>();
}
