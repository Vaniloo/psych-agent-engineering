package com.vanilo.psych.agent.controller;

import com.vanilo.psych.agent.exception.ServiceUnavailableException;
import com.vanilo.psych.agent.service.AgentRuntimeClient;
import com.vanilo.psych.agent.service.ConversationMemoryService;
import org.springframework.http.CacheControl;
import org.springframework.http.ResponseEntity;
import org.springframework.security.core.Authentication;
import org.springframework.web.bind.annotation.*;
import java.util.Map;

@RestController
@RequestMapping("/agent/memory-updates")
public class MemoryUpdateController {
    private final ConversationMemoryService memory;
    private final AgentRuntimeClient runtime;

    public MemoryUpdateController(ConversationMemoryService memory, AgentRuntimeClient runtime) {
        this.memory = memory;
        this.runtime = runtime;
    }

    @GetMapping("/{requestId}")
    public ResponseEntity<?> status(@PathVariable String requestId, Authentication authentication) {
        if (authentication == null) throw new org.springframework.web.server.ResponseStatusException(
                org.springframework.http.HttpStatus.UNAUTHORIZED);
        String owner = authentication.getName();
        memory.requireSavedTurn(owner, requestId); // Reject missing/foreign requests before proxying.
        Object result;
        try {
            result = runtime.memoryUpdateStatus(owner, requestId);
        } catch (ServiceUnavailableException exception) {
            result = Map.of("status", "unconfirmed");
        }
        return ResponseEntity.ok().cacheControl(CacheControl.noStore()).body(result);
    }
}
