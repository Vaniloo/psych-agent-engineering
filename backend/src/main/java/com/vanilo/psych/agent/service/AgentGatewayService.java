package com.vanilo.psych.agent.service;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.vanilo.psych.agent.dto.AgentChatRequest;
import com.vanilo.psych.agent.dto.AgentChatResponse;
import com.vanilo.psych.agent.dto.AgentRunMetricsResponse;
import com.vanilo.psych.agent.dto.AgentRuntimeRequest;
import com.vanilo.psych.agent.dto.AgentTurnPersistRequest;
import com.vanilo.psych.agent.exception.ServiceUnavailableException;
import com.vanilo.psych.agent.exception.CapacityExceededException;

import org.springframework.beans.factory.annotation.Qualifier;
import org.springframework.stereotype.Service;
import org.springframework.scheduling.concurrent.ThreadPoolTaskExecutor;
import org.springframework.web.servlet.mvc.method.annotation.SseEmitter;

import java.io.IOException;
import java.time.OffsetDateTime;
import java.util.List;
import java.util.Map;
import java.util.UUID;
import java.util.concurrent.Executor;
import java.util.concurrent.RejectedExecutionException;
import java.util.concurrent.Semaphore;
import java.util.concurrent.atomic.AtomicBoolean;
import java.util.concurrent.atomic.AtomicReference;
import java.util.concurrent.atomic.AtomicLong;

@Service
public class AgentGatewayService {
    private static final long STREAM_TIMEOUT_MS = 180_000L;

    private final AgentRuntimeClient runtimeClient;
    private final ConversationMemoryService conversationMemoryService;
    private final RiskDetectionService riskDetectionService;
    private final CrisisSupportService crisisSupportService;
    private final CrisisWorkflowService crisisWorkflowService;
    private final ObjectMapper objectMapper;
    private final AgentMetricsService agentMetricsService;
    private final Executor streamExecutor;
    private final Semaphore normalGenerationSlots;
    private final int generationCapacity;
    private final AtomicLong admissionRejections = new AtomicLong();

    public AgentGatewayService(
            AgentRuntimeClient runtimeClient,
            ConversationMemoryService conversationMemoryService,
            RiskDetectionService riskDetectionService,
            CrisisSupportService crisisSupportService,
            CrisisWorkflowService crisisWorkflowService,
            AgentMetricsService agentMetricsService,
            ObjectMapper objectMapper,
            @Qualifier("agentStreamExecutor") Executor streamExecutor) {
        this.runtimeClient = runtimeClient;
        this.conversationMemoryService = conversationMemoryService;
        this.riskDetectionService = riskDetectionService;
        this.crisisSupportService = crisisSupportService;
        this.crisisWorkflowService = crisisWorkflowService;
        this.agentMetricsService = agentMetricsService;
        this.objectMapper = objectMapper;
        this.streamExecutor = streamExecutor;
        this.generationCapacity = streamExecutor instanceof ThreadPoolTaskExecutor pool ? pool.getCorePoolSize() : 4;
        this.normalGenerationSlots = new Semaphore(generationCapacity);
    }

    public AgentChatResponse chat(AgentChatRequest request, String username) {
        String message = validateMessage(request);
        if (riskDetectionService.isHighRisk(message)) {
            return handleCrisis(request, username, message);
        }

        if (!normalGenerationSlots.tryAcquire()) {
            admissionRejections.incrementAndGet();
            throw new CapacityExceededException();
        }
        try {
            Long sessionId = conversationMemoryService.resolveSessionId(username, request.getSessionId(), message);
            AgentRuntimeRequest runtimeRequest = new AgentRuntimeRequest(username, sessionId, message, UUID.randomUUID().toString());
            try {
                AgentChatResponse response = runtimeClient.chat(runtimeRequest);
                agentMetricsService.recordRun(username, sessionId, response.getMetrics());
                return response;
            } catch (ServiceUnavailableException exception) {
                return unavailableReply(sessionId);
            }
        } finally {
            normalGenerationSlots.release();
        }
    }

    public SseEmitter stream(AgentChatRequest request, String username) {
        String message = validateMessage(request);
        SseEmitter emitter = new SseEmitter(STREAM_TIMEOUT_MS);
        AtomicBoolean completed = new AtomicBoolean(false);
        emitter.onCompletion(() -> completed.set(true));
        emitter.onTimeout(() -> completed.set(true));
        emitter.onError(error -> completed.set(true));
        boolean crisis = riskDetectionService.isHighRisk(message);

        Runnable action = () -> {
                    if (completed.get()) return;
                    boolean acquired = crisis || normalGenerationSlots.tryAcquire();
                    try {
                        if (!acquired) {
                            admissionRejections.incrementAndGet();
                            throw new CapacityExceededException();
                        }
                        if (crisis) {
                            streamCrisis(emitter, request, username, message);
                        } else {
                            streamRuntimeOrFallback(emitter, request, username, message);
                        }
                        if (completed.compareAndSet(false, true)) {
                            emitter.complete();
                        }
                    } catch (Exception exception) {
                        if (completed.compareAndSet(false, true)) {
                            try {
                                send(
                                        emitter,
                                        "error",
                                        Map.of(
                                                "message",
                                                exception instanceof CapacityExceededException
                                                    ? exception.getMessage() : "Agent 流式请求失败，请稍后重试",
                                                "type",
                                                exception.getClass().getSimpleName(),
                                                "code", exception instanceof CapacityExceededException
                                                    ? "ADMISSION_BUSY" : "AGENT_STREAM_ERROR"));
                            } catch (Exception ignored) {
                            }
                            emitter.completeWithError(exception);
                        }
                    } finally {
                        if (acquired && !crisis) normalGenerationSlots.release();
                    }
                };
        // This deterministic path does not call a model and must not wait behind
        // ordinary generation or be rejected because its queue is full.
        if (crisis) {
            action.run();
        } else {
            try {
                streamExecutor.execute(action);
            } catch (RejectedExecutionException exception) {
                admissionRejections.incrementAndGet();
                throw new CapacityExceededException();
            }
        }
        return emitter;
    }

    public Map<String, Object> capacitySnapshot() {
        int queued = 0, queueCapacity = 0;
        if (streamExecutor instanceof ThreadPoolTaskExecutor pool) {
            queued = pool.getThreadPoolExecutor().getQueue().size();
            queueCapacity = pool.getQueueCapacity();
        }
        return Map.of("concurrency", generationCapacity,
                "active", generationCapacity - normalGenerationSlots.availablePermits(),
                "queued", queued, "queueCapacity", queueCapacity,
                "rejectedSinceStart", admissionRejections.get(),
                "scope", "server_since_restart");
    }

    private void streamRuntimeOrFallback(
            SseEmitter emitter, AgentChatRequest request, String username, String message) {
        Long sessionId =
                conversationMemoryService.resolveSessionId(
                        username, request.getSessionId(), message);
        AgentRuntimeRequest runtimeRequest =
                new AgentRuntimeRequest(username, sessionId, message, UUID.randomUUID().toString());
        AtomicBoolean tokenEmitted = new AtomicBoolean(false);
        AtomicBoolean doneEmitted = new AtomicBoolean(false);
        AtomicReference<JsonNode> runtimeError = new AtomicReference<>();

        try {
            runtimeClient.stream(
                    runtimeRequest,
                    (event, payload) -> {
                        if ("token".equals(event)) {
                            tokenEmitted.set(true);
                        }
                        if ("done".equals(event)) {
                            doneEmitted.set(true);
                            try {
                                AgentChatResponse response =
                                        objectMapper.treeToValue(payload, AgentChatResponse.class);
                                agentMetricsService.recordRun(
                                        username, sessionId, response.getMetrics());
                            } catch (Exception ignored) {
                            }
                        }
                        if ("error".equals(event)) {
                            runtimeError.set(payload);
                            return;
                        }
                        sendUnchecked(emitter, event, payload);
                    });
            if (runtimeError.get() != null && !tokenEmitted.get()) {
                streamUnavailableFallback(emitter, username, message, sessionId);
                return;
            }
            if (runtimeError.get() != null) {
                sendUnchecked(emitter, "error", runtimeError.get());
                return;
            }
            if (!doneEmitted.get()) {
                throw new ServiceUnavailableException("Agent Runtime 流提前结束");
            }
        } catch (ServiceUnavailableException exception) {
            if (tokenEmitted.get()) {
                sendUnchecked(
                        emitter,
                        "error",
                        Map.of(
                                "message",
                                "流式回复中断，请重新发送",
                                "type",
                                exception.getClass().getSimpleName()));
                return;
            }
            streamUnavailableFallback(emitter, username, message, sessionId);
        }
    }

    private void streamUnavailableFallback(
            SseEmitter emitter, String username, String message, Long sessionId) {
        sendUnchecked(
                emitter,
                "status",
                Map.of(
                        "node", "fallback",
                        "label", "对话服务暂不可用"));
        AgentChatResponse response = unavailableReply(sessionId);
        sendUnchecked(emitter, "token", Map.of("text", response.getReply()));
        sendUnchecked(emitter, "done", response);
    }

    private AgentChatResponse unavailableReply(Long sessionId) {
        // A lost runtime response may follow a successful commit. Do not append
        // a second turn under a new request ID merely to save an outage notice.
        String reply = "对话服务暂时不可用。你可以稍后再试；如果现在很难受，也可以查看帮助中心的支持资源。";
        return new AgentChatResponse(reply, false, null, sessionId);
    }

    private AgentChatResponse handleCrisis(
            AgentChatRequest request, String username, String message) {
        long started = System.nanoTime();
        String startedAt = OffsetDateTime.now().toString();
        String requestId = UUID.randomUUID().toString();
        Long sessionId =
                conversationMemoryService.resolveSessionId(
                        username, request.getSessionId(), message);
        double sessionMs = elapsedMs(started);
        long supportStarted = System.nanoTime();
        String reply = crisisSupportService.crisisReply();
        crisisWorkflowService.handle(username, message);
        var resources = crisisSupportService.resources();
        double supportMs = elapsedMs(supportStarted);
        long persistStarted = System.nanoTime();
        conversationMemoryService.rememberAgentTurn(
                new AgentTurnPersistRequest(
                        requestId, username, sessionId, message, reply, "", "", "", Map.of()));
        double persistMs = elapsedMs(persistStarted);
        var response =
                new AgentChatResponse(
                        reply,
                        true,
                        "crisis_support",
                        sessionId,
                        true,
                        crisisSupportService.helpCenterUrl(),
                        resources,
                        null);
        var metrics = new AgentRunMetricsResponse();
        metrics.setRequestId(requestId);
        metrics.setStartedAt(startedAt);
        metrics.setModel("rules-crisis-support");
        metrics.setStatus("success");
        metrics.setTotalDurationMs(elapsedMs(started));
        metrics.setTtftMs(metrics.getTotalDurationMs());
        metrics.setPromptTokens(0L);
        metrics.setCompletionTokens(0L);
        metrics.setTotalTokens(0L);
        metrics.setModelCallCount(0);
        metrics.setModelCalls(List.of());
        metrics.setTokensPerSecond(0.0);
        metrics.setActualCostUsd(0.0);
        metrics.setEstimatedCostUsd(0.0);
        metrics.setCostMode("local");
        metrics.setMcpDurationMs(0.0);
        metrics.setFallbackCount(0);
        metrics.setPersistenceStatus("saved");
        metrics.setRetrieval(Map.of());
        metrics.setNodeDurationsMs(
                Map.of(
                        "resolve_session",
                        sessionMs,
                        "crisis_support",
                        supportMs,
                        "persist_turn",
                        persistMs));
        metrics.setCapabilityCalls(
                List.of(
                        Map.of(
                                "name",
                                "crisis_support",
                                "transport",
                                "local",
                                "durationMs",
                                supportMs,
                                "status",
                                "ok"),
                        Map.of(
                                "name",
                                "persist_turn",
                                "transport",
                                "local",
                                "durationMs",
                                persistMs,
                                "status",
                                "ok")));
        metrics.setPipeline(
                Map.of(
                        "router",
                                Map.of(
                                        "provider",
                                        "rules",
                                        "decisionProvider",
                                        "rules",
                                        "route",
                                        "crisis",
                                        "riskBand",
                                        "urgent",
                                        "supportLevel",
                                        "crisis",
                                        "fallback",
                                        false),
                        "executor",
                                Map.of(
                                        "provider",
                                        "rules",
                                        "decisions",
                                        List.of(
                                                Map.of(
                                                        "step",
                                                        1,
                                                        "action",
                                                        "crisis_support",
                                                        "provider",
                                                        "rules",
                                                        "fallback",
                                                        ""))),
                        "memory", Map.of("provider", "outbox", "delivery", "queued")));
        response.setMetrics(metrics);
        agentMetricsService.recordRun(username, sessionId, metrics);
        return response;
    }

    private static double elapsedMs(long started) {
        return (System.nanoTime() - started) / 1_000_000.0;
    }

    private void streamCrisis(
            SseEmitter emitter, AgentChatRequest request, String username, String message) {
        sendUnchecked(
                emitter,
                "status",
                Map.of(
                        "node", "safety_guard",
                        "label", "安全规则已触发，进入危机支持流程"));
        AgentChatResponse response = handleCrisis(request, username, message);
        sendUnchecked(emitter, "token", Map.of("text", response.getReply()));
        sendUnchecked(emitter, "metrics", response.getMetrics());
        sendUnchecked(emitter, "done", response);
    }

    private String validateMessage(AgentChatRequest request) {
        if (request == null || request.getMessage() == null || request.getMessage().isBlank()) {
            throw new RuntimeException("message不能为空");
        }
        String message = request.getMessage().strip();
        if (message.length() > 4000) {
            throw new RuntimeException("message不能超过4000个字符");
        }
        return message;
    }

    private void sendUnchecked(SseEmitter emitter, String event, Object data) {
        try {
            send(emitter, event, data);
        } catch (IOException exception) {
            throw new ServiceUnavailableException("SSE 客户端连接已断开", exception);
        }
    }

    private void send(SseEmitter emitter, String event, Object data) throws IOException {
        Object payload = data instanceof JsonNode ? data : objectMapper.valueToTree(data);
        emitter.send(SseEmitter.event().name(event).data(payload));
    }
}
