package com.vanilo.psych.agent.service;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.vanilo.psych.agent.controller.MemoryUpdateController;
import com.vanilo.psych.agent.exception.ServiceUnavailableException;
import org.junit.jupiter.api.Test;
import org.springframework.security.authentication.UsernamePasswordAuthenticationToken;
import org.springframework.web.server.ResponseStatusException;
import static org.junit.jupiter.api.Assertions.*;
import static org.mockito.Mockito.*;

class MemoryUpdateControllerTests {
    @Test void missingOrForeignTurnNeverReachesRuntime() {
        var memory = mock(ConversationMemoryService.class);
        var runtime = mock(AgentRuntimeClient.class);
        var controller = new MemoryUpdateController(memory, runtime);
        var auth = new UsernamePasswordAuthenticationToken("alice", "unused");
        doThrow(new ResponseStatusException(org.springframework.http.HttpStatus.NOT_FOUND))
                .when(memory).requireSavedTurn("alice", "foreign");
        assertThrows(ResponseStatusException.class, () -> controller.status("foreign", auth));
        verifyNoInteractions(runtime);
        assertThrows(ResponseStatusException.class, () -> controller.status("any", null));
    }

    @Test void authenticatedOwnerIsForwardedAndResponsesCannotBeCached() throws Exception {
        var memory = mock(ConversationMemoryService.class);
        var runtime = mock(AgentRuntimeClient.class);
        var json = new ObjectMapper().readTree("{\"status\":\"updated\",\"changedCount\":1}");
        when(runtime.memoryUpdateStatus("alice", "owned")).thenReturn(json);
        var response = new MemoryUpdateController(memory, runtime).status("owned",
                new UsernamePasswordAuthenticationToken("alice", "unused"));
        verify(memory).requireSavedTurn("alice", "owned");
        assertEquals(json, response.getBody());
        assertEquals("no-store", response.getHeaders().getCacheControl());
    }

    @Test void runtimeOutageDoesNotClaimMemoryUpdated() {
        var memory = mock(ConversationMemoryService.class);
        var runtime = mock(AgentRuntimeClient.class);
        when(runtime.memoryUpdateStatus("alice", "owned"))
                .thenThrow(new ServiceUnavailableException("offline"));
        var response = new MemoryUpdateController(memory, runtime).status("owned",
                new UsernamePasswordAuthenticationToken("alice", "unused"));
        assertEquals(java.util.Map.of("status", "unconfirmed"), response.getBody());
    }
}
