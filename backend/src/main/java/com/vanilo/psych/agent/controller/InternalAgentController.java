package com.vanilo.psych.agent.controller;

import com.vanilo.psych.agent.dto.AgentContextResponse;
import com.vanilo.psych.agent.dto.AgentTurnPersistRequest;
import com.vanilo.psych.agent.dto.InternalCrisisRequest;
import com.vanilo.psych.agent.dto.InternalRiskScanRequest;
import com.vanilo.psych.agent.security.InternalServiceAuthenticator;
import com.vanilo.psych.agent.service.ConversationMemoryService;
import com.vanilo.psych.agent.service.CrisisSupportService;
import com.vanilo.psych.agent.service.CrisisWorkflowService;
import com.vanilo.psych.agent.service.RiskDetectionService;

import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestHeader;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;

import java.util.Map;

@RestController
@RequestMapping("/internal/agent")
public class InternalAgentController {
    private static final String TOKEN_HEADER = "X-Internal-Service-Token";

    private final InternalServiceAuthenticator authenticator;
    private final ConversationMemoryService conversationMemoryService;
    private final RiskDetectionService riskDetectionService;
    private final CrisisWorkflowService crisisWorkflowService;
    private final CrisisSupportService crisisSupportService;

    public InternalAgentController(
            InternalServiceAuthenticator authenticator,
            ConversationMemoryService conversationMemoryService,
            RiskDetectionService riskDetectionService,
            CrisisWorkflowService crisisWorkflowService,
            CrisisSupportService crisisSupportService) {
        this.authenticator = authenticator;
        this.conversationMemoryService = conversationMemoryService;
        this.riskDetectionService = riskDetectionService;
        this.crisisWorkflowService = crisisWorkflowService;
        this.crisisSupportService = crisisSupportService;
    }

    @GetMapping("/context")
    public AgentContextResponse context(
            @RequestHeader(TOKEN_HEADER) String token,
            @RequestParam String username,
            @RequestParam Long sessionId,
            @RequestParam(defaultValue = "40") int messageLimit) {
        authenticator.verify(token);
        return conversationMemoryService.getAgentContext(username, sessionId, messageLimit);
    }

    @GetMapping("/recent-messages")
    public java.util.List<Map<String, Object>> recentMessages(
            @RequestHeader(TOKEN_HEADER) String token, @RequestParam String username) {
        authenticator.verify(token);
        return conversationMemoryService.recentUserEvidence(username);
    }

    @PostMapping("/risk-scan")
    public Map<String, Object> riskScan(
            @RequestHeader(TOKEN_HEADER) String token,
            @RequestBody InternalRiskScanRequest request) {
        authenticator.verify(token);
        boolean highRisk =
                riskDetectionService.isHighRisk(request == null ? null : request.getText());
        return Map.of(
                "highRisk", highRisk,
                "risk", highRisk ? "high" : "low",
                "score", highRisk ? 1.0 : 0.0,
                "action", highRisk ? "crisis_response" : "continue");
    }

    @PostMapping("/persist-turn")
    public Map<String, Object> persistTurn(
            @RequestHeader(TOKEN_HEADER) String token,
            @RequestBody AgentTurnPersistRequest request) {
        authenticator.verify(token);
        conversationMemoryService.rememberAgentTurn(request);
        return Map.of("saved", true, "sessionId", request.getSessionId());
    }

    @PostMapping("/crisis")
    public Map<String, Object> crisis(
            @RequestHeader(TOKEN_HEADER) String token, @RequestBody InternalCrisisRequest request) {
        authenticator.verify(token);
        crisisWorkflowService.handle(request.getUsername(), request.getMessage());
        return Map.of(
                "reply", crisisSupportService.crisisReply(),
                "helpCenterUrl", crisisSupportService.helpCenterUrl(),
                "resources", crisisSupportService.resources());
    }
}
