package com.vanilo.psych.agent.controller;

import com.vanilo.psych.agent.dto.MemoryUpdateRequest;
import com.vanilo.psych.agent.repository.AgentTurnReceiptMapper;
import com.vanilo.psych.agent.security.InternalServiceAuthenticator;
import com.vanilo.psych.agent.service.ConversationMemoryService;

import org.springframework.transaction.annotation.Transactional;
import org.springframework.web.bind.annotation.*;

import java.util.List;
import java.util.Map;

@RestController
@RequestMapping("/internal/agent/memory-jobs")
public class MemoryOutboxController {
    private final AgentTurnReceiptMapper receipts;
    private final InternalServiceAuthenticator auth;
    private final ConversationMemoryService memoryService;

    public MemoryOutboxController(
            AgentTurnReceiptMapper receipts, InternalServiceAuthenticator auth,
            ConversationMemoryService memoryService) {
        this.receipts = receipts;
        this.auth = auth;
        this.memoryService = memoryService;
    }

    @GetMapping
    @Transactional(readOnly = true)
    public List<Map<String, Object>> pending(
            @RequestHeader("X-Internal-Service-Token") String token) {
        auth.verify(token);
        return receipts.memoryJobs("PENDING", 20).stream()
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
                                        item.getCreatedAt().atZone(java.time.ZoneId.systemDefault()).toOffsetDateTime().toString()))
                .toList();
    }

    @PostMapping("/{id}/complete")
    public Map<String, Object> complete(
            @RequestHeader("X-Internal-Service-Token") String token,
            @PathVariable Long id,
            @RequestBody(required = false) MemoryUpdateRequest update) {
        auth.verify(token);
        return Map.of("saved", true, "applied", memoryService.completeMemoryJob(id, update));
    }
}
