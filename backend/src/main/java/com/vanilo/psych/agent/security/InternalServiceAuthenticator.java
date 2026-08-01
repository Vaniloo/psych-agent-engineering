package com.vanilo.psych.agent.security;

import com.vanilo.psych.agent.config.AgentRuntimeProperties;
import org.springframework.http.HttpStatus;
import org.springframework.stereotype.Component;
import org.springframework.web.server.ResponseStatusException;

import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;

@Component
public class InternalServiceAuthenticator {
    private final AgentRuntimeProperties properties;

    public InternalServiceAuthenticator(AgentRuntimeProperties properties) {
        this.properties = properties;
    }

    public void verify(String suppliedToken) {
        String expectedToken = properties.getInternalServiceToken();
        if (expectedToken == null || expectedToken.isBlank() || suppliedToken == null
                || !MessageDigest.isEqual(
                expectedToken.getBytes(StandardCharsets.UTF_8),
                suppliedToken.getBytes(StandardCharsets.UTF_8))) {
            throw new ResponseStatusException(HttpStatus.UNAUTHORIZED, "内部服务认证失败");
        }
    }
}
