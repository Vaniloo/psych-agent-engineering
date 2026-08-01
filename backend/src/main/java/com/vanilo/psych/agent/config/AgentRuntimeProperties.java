package com.vanilo.psych.agent.config;

import lombok.Data;
import org.springframework.boot.context.properties.ConfigurationProperties;
import org.springframework.stereotype.Component;

@Data
@Component
@ConfigurationProperties(prefix = "psych.agent-runtime")
public class AgentRuntimeProperties {
    private String baseUrl = "http://localhost:8090";
    private String internalServiceToken = "change-internal-service-token";
    private int connectTimeoutMs = 3000;
    private int requestTimeoutMs = 180000;
}
