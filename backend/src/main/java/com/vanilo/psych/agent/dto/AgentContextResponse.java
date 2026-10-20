package com.vanilo.psych.agent.dto;

import lombok.AllArgsConstructor;
import lombok.Data;
import lombok.NoArgsConstructor;

import java.util.List;

@Data
@NoArgsConstructor
@AllArgsConstructor
public class AgentContextResponse {
    private java.util.Map<String,Object> roleConfiguration=java.util.Map.of();
    public AgentContextResponse(String username,Long sessionId,String sessionSummary,String mediumTermSummary,String longTermMemory,String rolePrompt,UserProfileResponse profile,List<ConversationMessageResponse> messages) {
        this(java.util.Map.of(),username,sessionId,sessionSummary,mediumTermSummary,longTermMemory,rolePrompt,profile,messages);
    }
    private String username;
    private Long sessionId;
    private String sessionSummary;
    private String mediumTermSummary;
    private String longTermMemory;
    private String rolePrompt;
    private UserProfileResponse profile;
    private List<ConversationMessageResponse> messages;
}
