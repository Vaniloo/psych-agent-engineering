package com.vanilo.psych.agent.controller;

import com.vanilo.psych.agent.repository.AgentTurnReceiptRepository;
import com.vanilo.psych.agent.security.InternalServiceAuthenticator;

import org.springframework.data.domain.PageRequest;
import org.springframework.transaction.annotation.Transactional;
import org.springframework.web.bind.annotation.*;

import java.util.List;
import java.util.Map;

@RestController
@RequestMapping("/internal/agent/memory-jobs")
public class MemoryOutboxController {
    private final AgentTurnReceiptRepository receipts;
    private final InternalServiceAuthenticator auth;

    public MemoryOutboxController(
            AgentTurnReceiptRepository receipts, InternalServiceAuthenticator auth) {
        this.receipts = receipts;
        this.auth = auth;
    }

    @GetMapping
    @Transactional(readOnly = true)
    public List<Map<String, Object>> pending(
            @RequestHeader("X-Internal-Service-Token") String token) {
        auth.verify(token);
        return receipts.findByMemoryStatusOrderByIdAsc("PENDING", PageRequest.of(0, 20)).stream()
                .map(
                        item ->
                                Map.<String, Object>of(
                                        "id",
                                        item.getId(),
                                        "username",
                                        item.getUser().getUsername(),
                                        "sessionId",
                                        item.getSessionId(),
                                        "requestId",
                                        item.getRequestId(),
                                        "userMessage",
                                        item.getUserMessage(),
                                        "assistantReply",
                                        item.getAssistantReply(),
                                        "createdAt",
                                        item.getCreatedAt().toString()))
                .toList();
    }

    @PostMapping("/{id}/complete")
    @Transactional
    public Map<String, Object> complete(
            @RequestHeader("X-Internal-Service-Token") String token, @PathVariable Long id) {
        auth.verify(token);
        if (receipts.completeMemoryJob(id) == 0) {
            throw new org.springframework.web.server.ResponseStatusException(
                    org.springframework.http.HttpStatus.NOT_FOUND, "记忆任务不存在");
        }
        return Map.of("saved", true);
    }
}
