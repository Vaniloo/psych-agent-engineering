package com.vanilo.psych.agent.controller;

import com.vanilo.psych.agent.dto.AgentChatRequest;
import com.vanilo.psych.agent.dto.AgentChatResponse;
import com.vanilo.psych.agent.service.AgentGatewayService;
import org.springframework.http.MediaType;
import org.springframework.security.core.Authentication;
import org.springframework.web.bind.annotation.*;
import org.springframework.web.servlet.mvc.method.annotation.SseEmitter;

@RestController
@RequestMapping("/agent")
public class AgentController {
    private final AgentGatewayService agentGatewayService;
    public AgentController(AgentGatewayService agentGatewayService) {
        this.agentGatewayService = agentGatewayService;
    }
    @PostMapping("/chat")
    public AgentChatResponse chat(@RequestBody AgentChatRequest agentChatRequest,
                                  Authentication authentication) {
        if (authentication == null) {
            throw new RuntimeException("未登录");
        }
        return agentGatewayService.chat(agentChatRequest, authentication.getName());
    }

    @PostMapping(value = "/chat/stream", produces = MediaType.TEXT_EVENT_STREAM_VALUE)
    public SseEmitter stream(@RequestBody AgentChatRequest agentChatRequest,
                             Authentication authentication) {
        if (authentication == null) {
            throw new RuntimeException("未登录");
        }
        return agentGatewayService.stream(agentChatRequest, authentication.getName());
    }
}
