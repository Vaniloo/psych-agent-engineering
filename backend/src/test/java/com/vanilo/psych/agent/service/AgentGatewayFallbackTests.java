package com.vanilo.psych.agent.service;

import static org.junit.jupiter.api.Assertions.*;
import static org.mockito.ArgumentMatchers.*;
import static org.mockito.Mockito.*;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.vanilo.psych.agent.dto.AgentChatRequest;
import com.vanilo.psych.agent.exception.ServiceUnavailableException;

import org.junit.jupiter.api.Test;

class AgentGatewayFallbackTests {
    @Test
    void ambiguousRuntimeFailureDoesNotAppendASecondTurn() {
        var runtime = mock(AgentRuntimeClient.class);
        var memory = mock(ConversationMemoryService.class);
        when(memory.resolveSessionId("alice", 1L, "你好")).thenReturn(1L);
        when(runtime.chat(any()))
                .thenThrow(new ServiceUnavailableException("response lost after commit"));
        var gateway =
                new AgentGatewayService(
                        runtime,
                        memory,
                        mock(RiskDetectionService.class),
                        mock(CrisisSupportService.class),
                        mock(CrisisWorkflowService.class),
                        mock(AgentMetricsService.class),
                        new ObjectMapper(),
                        Runnable::run);
        var request = new AgentChatRequest();
        request.setMessage("你好");
        request.setSessionId(1L);
        var response = gateway.chat(request, "alice");
        assertEquals(1L, response.getSessionId());
        assertTrue(response.getReply().contains("暂时不可用"));
        verify(memory, never()).rememberAgentTurn(any());
    }
}
