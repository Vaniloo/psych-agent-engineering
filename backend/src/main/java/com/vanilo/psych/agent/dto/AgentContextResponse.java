package com.vanilo.psych.agent.dto;

import lombok.AllArgsConstructor;
import lombok.Data;
import lombok.NoArgsConstructor;

import java.util.List;

@Data
@NoArgsConstructor
@AllArgsConstructor
public class AgentContextResponse {
    private String username;
    private Long sessionId;
    private String sessionSummary;
    private String mediumTermSummary;
    private String longTermMemory;
    private String rolePrompt;
    private UserProfileResponse profile;
    private List<ConversationMessageResponse> messages;
}
