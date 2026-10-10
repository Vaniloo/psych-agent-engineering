package com.vanilo.psych.agent.service;

import static org.junit.jupiter.api.Assertions.*;
import static org.mockito.ArgumentMatchers.*;
import static org.mockito.Mockito.*;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.vanilo.psych.agent.dto.AgentChatRequest;
import com.vanilo.psych.agent.dto.AgentRunMetricsResponse;
import com.vanilo.psych.agent.dto.AgentTurnPersistRequest;
import com.vanilo.psych.agent.exception.ServiceUnavailableException;

import org.junit.jupiter.api.Test;
import org.junit.jupiter.params.ParameterizedTest;
import org.junit.jupiter.params.provider.ValueSource;
import org.mockito.ArgumentCaptor;

import java.util.List;

class AgentGatewayFallbackTests {
    @ParameterizedTest
    @ValueSource(booleans = {false, true})
    void crisisShortcutPersistsAndRecordsRulesInsteadOfInventingModelUse(boolean stream) {
        var runtime = mock(AgentRuntimeClient.class);
        var memory = mock(ConversationMemoryService.class);
        var risk = mock(RiskDetectionService.class);
        var support = mock(CrisisSupportService.class);
        var workflow = mock(CrisisWorkflowService.class);
        var metricsService = mock(AgentMetricsService.class);
        when(risk.isHighRisk("危机测试")).thenReturn(true);
        when(memory.resolveSessionId("alice", 1L, "危机测试")).thenReturn(1L);
        when(support.crisisReply()).thenReturn("固定危机支持");
        when(support.resources()).thenReturn(List.of());
        var gateway =
                new AgentGatewayService(
                        runtime,
                        memory,
                        risk,
                        support,
                        workflow,
                        metricsService,
                        new ObjectMapper(),
                        Runnable::run);
        var request = new AgentChatRequest();
        request.setMessage("危机测试");
        request.setSessionId(1L);
        if (stream) gateway.stream(request, "alice");
        else {
            var response = gateway.chat(request, "alice");
            assertTrue(response.isCrisis());
            assertEquals("crisis_support", response.getToolName());
            assertNotNull(response.getMetrics());
        }
        var saved = ArgumentCaptor.forClass(AgentTurnPersistRequest.class);
        verify(memory).rememberAgentTurn(saved.capture());
        var measured = ArgumentCaptor.forClass(AgentRunMetricsResponse.class);
        verify(metricsService).recordRun(eq("alice"), eq(1L), measured.capture());
        var metrics = measured.getValue();
        assertEquals(saved.getValue().getRequestId(), metrics.getRequestId());
        assertEquals("saved", metrics.getPersistenceStatus());
        assertEquals("rules-crisis-support", metrics.getModel());
        assertEquals(0, metrics.getModelCallCount());
        assertEquals(0L, metrics.getTotalTokens());
        assertTrue(metrics.getCapabilityCalls().stream().allMatch(call -> "ok".equals(call.get("status"))));
        assertTrue(metrics.getTotalDurationMs() >= 0);
        assertEquals(
                "queued",
                ((java.util.Map<?, ?>) metrics.getPipeline().get("memory")).get("delivery"));
        verifyNoInteractions(runtime);
    }

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
