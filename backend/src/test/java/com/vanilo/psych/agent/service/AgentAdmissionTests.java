package com.vanilo.psych.agent.service;

import static org.junit.jupiter.api.Assertions.*;
import static org.mockito.ArgumentMatchers.*;
import static org.mockito.Mockito.*;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.vanilo.psych.agent.config.AsyncConfig;
import com.vanilo.psych.agent.dto.AgentChatRequest;
import com.vanilo.psych.agent.dto.AgentChatResponse;
import com.vanilo.psych.agent.exception.CapacityExceededException;
import com.vanilo.psych.agent.exception.GlobalExceptionHandler;
import org.junit.jupiter.api.Test;
import org.springframework.scheduling.concurrent.ThreadPoolTaskExecutor;
import org.springframework.http.MediaType;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RestController;
import org.springframework.test.web.servlet.setup.MockMvcBuilders;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.post;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.*;

import java.util.List;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.RejectedExecutionException;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.Executors;

class AgentAdmissionTests {
    @RestController
    static class FullQueueEndpoint {
        @PostMapping(value = "/stream", produces = MediaType.TEXT_EVENT_STREAM_VALUE)
        Object stream() { throw new CapacityExceededException(); }
    }

    @Test
    void sseAcceptHeaderStillReceivesJson503AndRetryAfter() throws Exception {
        var mvc = MockMvcBuilders.standaloneSetup(new FullQueueEndpoint())
                .setControllerAdvice(new GlobalExceptionHandler()).build();
        mvc.perform(post("/stream").accept(MediaType.TEXT_EVENT_STREAM))
                .andExpect(status().isServiceUnavailable())
                .andExpect(header().string("Retry-After", "2"))
                .andExpect(content().contentTypeCompatibleWith(MediaType.APPLICATION_JSON))
                .andExpect(jsonPath("$.success").value(false))
                .andExpect(jsonPath("$.code").value("ADMISSION_BUSY"));
    }
    @Test
    void fixedActiveAndShortQueueRejectInsteadOfGrowingHiddenBacklog() throws Exception {
        var executor = (ThreadPoolTaskExecutor) new AsyncConfig().agentStreamExecutor(1, 1);
        var entered = new CountDownLatch(1);
        var release = new CountDownLatch(1);
        try {
            executor.execute(() -> {
                entered.countDown();
                try { release.await(); } catch (InterruptedException e) { Thread.currentThread().interrupt(); }
            });
            assertTrue(entered.await(2, TimeUnit.SECONDS));
            executor.execute(() -> {});
            assertThrows(RejectedExecutionException.class, () -> executor.execute(() -> {}));
            assertEquals(1, executor.getActiveCount());
            assertEquals(1, executor.getThreadPoolExecutor().getQueue().size());
        } finally {
            release.countDown();
            executor.shutdown();
        }
    }

    @Test
    void overloadedNormalStreamDoesNotResolveSessionOrCallModelAndReturnsRetryHint() {
        var memory = mock(ConversationMemoryService.class);
        var runtime = mock(AgentRuntimeClient.class);
        var gateway = new AgentGatewayService(runtime, memory, mock(RiskDetectionService.class),
                mock(CrisisSupportService.class), mock(CrisisWorkflowService.class),
                mock(AgentMetricsService.class), new ObjectMapper(),
                action -> { throw new RejectedExecutionException(); });
        var request = new AgentChatRequest();
        request.setMessage("测试普通请求");
        request.setSessionId(1L);
        var error = assertThrows(CapacityExceededException.class, () -> gateway.stream(request, "alice"));
        verifyNoInteractions(memory, runtime);
        var response = new GlobalExceptionHandler().handleCapacityExceeded(error);
        assertEquals(503, response.getStatusCode().value());
        assertEquals("2", response.getHeaders().getFirst("Retry-After"));
        assertEquals(1L, gateway.capacitySnapshot().get("rejectedSinceStart"));
    }

    @Test
    void deterministicCrisisSupportBypassesFullModelQueue() {
        var runtime = mock(AgentRuntimeClient.class);
        var memory = mock(ConversationMemoryService.class);
        var risk = mock(RiskDetectionService.class);
        var support = mock(CrisisSupportService.class);
        when(risk.isHighRisk("危机测试")).thenReturn(true);
        when(memory.resolveSessionId("alice", 1L, "危机测试")).thenReturn(1L);
        when(support.crisisReply()).thenReturn("固定支持");
        when(support.resources()).thenReturn(List.of());
        var gateway = new AgentGatewayService(runtime, memory, risk, support,
                mock(CrisisWorkflowService.class), mock(AgentMetricsService.class), new ObjectMapper(),
                action -> { throw new RejectedExecutionException(); });
        var request = new AgentChatRequest();
        request.setMessage("危机测试");
        request.setSessionId(1L);
        assertNotNull(gateway.stream(request, "alice"));
        verify(memory).rememberAgentTurn(any());
        verifyNoInteractions(runtime);
        assertEquals(0L, gateway.capacitySnapshot().get("rejectedSinceStart"));
    }

    @Test
    void synchronousChatSharesTheSameGenerationLimitAndReleasesSlots() throws Exception {
        var runtime = mock(AgentRuntimeClient.class);
        var memory = mock(ConversationMemoryService.class);
        var entered = new CountDownLatch(4);
        var release = new CountDownLatch(1);
        when(memory.resolveSessionId(anyString(), any(), anyString())).thenReturn(1L);
        when(runtime.chat(any())).thenAnswer(invocation -> {
            entered.countDown();
            release.await(2, TimeUnit.SECONDS);
            return new AgentChatResponse();
        });
        var gateway = new AgentGatewayService(runtime, memory, mock(RiskDetectionService.class),
                mock(CrisisSupportService.class), mock(CrisisWorkflowService.class),
                mock(AgentMetricsService.class), new ObjectMapper(), Runnable::run);
        var request = new AgentChatRequest(); request.setMessage("普通聊天测试"); request.setSessionId(1L);
        var clients = Executors.newFixedThreadPool(4);
        try {
            var futures = new java.util.ArrayList<java.util.concurrent.Future<?>>();
            for (int i=0;i<4;i++) futures.add(clients.submit(() -> gateway.chat(request, "alice")));
            assertTrue(entered.await(2, TimeUnit.SECONDS));
            assertEquals(4, gateway.capacitySnapshot().get("active"));
            assertThrows(CapacityExceededException.class, () -> gateway.chat(request, "alice"));
            verify(memory, times(4)).resolveSessionId(anyString(), any(), anyString());
            release.countDown();
            for (var future : futures) future.get(2, TimeUnit.SECONDS);
            assertEquals(0, gateway.capacitySnapshot().get("active"));
        } finally { release.countDown(); clients.shutdownNow(); }
    }
}
