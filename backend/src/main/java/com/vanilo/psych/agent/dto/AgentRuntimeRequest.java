package com.vanilo.psych.agent.dto;

import lombok.AllArgsConstructor;
import lombok.Data;
import lombok.NoArgsConstructor;

@Data
@NoArgsConstructor
@AllArgsConstructor
public class AgentRuntimeRequest {
    private String username;
    private Long sessionId;
    private String message;
    private String requestId;
}
