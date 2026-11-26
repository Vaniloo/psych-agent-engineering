package com.vanilo.psych.agent.dto;

import lombok.AllArgsConstructor;
import lombok.Data;
import lombok.NoArgsConstructor;

import java.util.List;

/** Layered context sent to the Python runtime: summaries, profile and recent messages. */
@Data
@NoArgsConstructor
@AllArgsConstructor
public class AgentContextResponse {
    private String username;
    private Long sessionId;
    private String sessionSummary;
    private String mediumTermSummary;
    private String longTermMemory;
    private UserProfileResponse profile;
    private List<ConversationMessageResponse> messages;
}
