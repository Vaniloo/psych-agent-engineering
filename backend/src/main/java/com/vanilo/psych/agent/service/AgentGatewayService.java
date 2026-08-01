package com.vanilo.psych.agent.service;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.vanilo.psych.agent.dto.AgentChatRequest;
import com.vanilo.psych.agent.dto.AgentChatResponse;
import com.vanilo.psych.agent.dto.AgentRuntimeRequest;
import com.vanilo.psych.agent.dto.AgentTurnPersistRequest;
import com.vanilo.psych.agent.exception.ServiceUnavailableException;
import org.springframework.beans.factory.annotation.Qualifier;
import org.springframework.stereotype.Service;
import org.springframework.web.servlet.mvc.method.annotation.SseEmitter;

import java.io.IOException;
import java.util.Map;
import java.util.UUID;
import java.util.concurrent.Executor;
import java.util.concurrent.atomic.AtomicBoolean;
import java.util.concurrent.atomic.AtomicReference;

@Service
public class AgentGatewayService {
    private static final long STREAM_TIMEOUT_MS = 180_000L;

    private final AgentRuntimeClient runtimeClient;
    private final AgentService legacyAgentService;
    private final ConversationMemoryService conversationMemoryService;
    private final RiskDetectionService riskDetectionService;
    private final CrisisSupportService crisisSupportService;
    private final CrisisWorkflowService crisisWorkflowService;
    private final ObjectMapper objectMapper;
    private final AgentMetricsService agentMetricsService;
    private final Executor streamExecutor;

    public AgentGatewayService(AgentRuntimeClient runtimeClient,
                               AgentService legacyAgentService,
                               ConversationMemoryService conversationMemoryService,
                               RiskDetectionService riskDetectionService,
                               CrisisSupportService crisisSupportService,
                               CrisisWorkflowService crisisWorkflowService,
                               AgentMetricsService agentMetricsService,
                               ObjectMapper objectMapper,
                               @Qualifier("agentStreamExecutor") Executor streamExecutor) {
        this.runtimeClient = runtimeClient;
        this.legacyAgentService = legacyAgentService;
        this.conversationMemoryService = conversationMemoryService;
        this.riskDetectionService = riskDetectionService;
        this.crisisSupportService = crisisSupportService;
        this.crisisWorkflowService = crisisWorkflowService;
        this.agentMetricsService = agentMetricsService;
        this.objectMapper = objectMapper;
        this.streamExecutor = streamExecutor;
    }

    public AgentChatResponse chat(AgentChatRequest request, String username) {
        String message = validateMessage(request);
        if (riskDetectionService.isHighRisk(message)) {
            return handleCrisis(request, username, message);
        }

        Long sessionId = conversationMemoryService.resolveSessionId(
                username,
                request.getSessionId(),
                message
        );
        AgentRuntimeRequest runtimeRequest = new AgentRuntimeRequest(
                username,
                sessionId,
                message,
                UUID.randomUUID().toString()
        );
        try {
            AgentChatResponse response = runtimeClient.chat(runtimeRequest);
            agentMetricsService.recordRun(username, sessionId, response.getMetrics());
            return response;
        } catch (ServiceUnavailableException exception) {
            return legacyAgentService.chat(new AgentChatRequest(message, sessionId), username);
        }
    }

    public SseEmitter stream(AgentChatRequest request, String username) {
        String message = validateMessage(request);
        SseEmitter emitter = new SseEmitter(STREAM_TIMEOUT_MS);
        AtomicBoolean completed = new AtomicBoolean(false);
        emitter.onCompletion(() -> completed.set(true));
        emitter.onTimeout(() -> completed.set(true));

        streamExecutor.execute(() -> {
            try {
                if (riskDetectionService.isHighRisk(message)) {
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
                        send(emitter, "error", Map.of(
                                "message", "Agent 流式请求失败，请稍后重试",
                                "type", exception.getClass().getSimpleName()
                        ));
                    } catch (Exception ignored) {
                    }
                    emitter.completeWithError(exception);
                }
            }
        });
        return emitter;
    }

    private void streamRuntimeOrFallback(SseEmitter emitter,
                                         AgentChatRequest request,
                                         String username,
                                         String message) {
        Long sessionId = conversationMemoryService.resolveSessionId(
                username,
                request.getSessionId(),
                message
        );
        AgentRuntimeRequest runtimeRequest = new AgentRuntimeRequest(
                username,
                sessionId,
                message,
                UUID.randomUUID().toString()
        );
        AtomicBoolean tokenEmitted = new AtomicBoolean(false);
        AtomicBoolean doneEmitted = new AtomicBoolean(false);
        AtomicReference<JsonNode> runtimeError = new AtomicReference<>();

        try {
            runtimeClient.stream(runtimeRequest, (event, payload) -> {
                if ("token".equals(event)) {
                    tokenEmitted.set(true);
                }
                if ("done".equals(event)) {
                    doneEmitted.set(true);
                    try {
                        AgentChatResponse response = objectMapper.treeToValue(
                                payload,
                                AgentChatResponse.class
                        );
                        agentMetricsService.recordRun(
                                username,
                                sessionId,
                                response.getMetrics()
                        );
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
                streamLegacyFallback(emitter, username, message, sessionId);
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
                sendUnchecked(emitter, "error", Map.of(
                        "message", "流式回复中断，请重新发送",
                        "type", exception.getClass().getSimpleName()
                ));
                return;
            }
            streamLegacyFallback(emitter, username, message, sessionId);
        }
    }

    private void streamLegacyFallback(SseEmitter emitter,
                                      String username,
                                      String message,
                                      Long sessionId) {
        sendUnchecked(emitter, "status", Map.of(
                "node", "fallback",
                "label", "LangGraph 运行时不可用，切换 Java Agent 降级链路"
        ));
        AgentChatResponse response = legacyAgentService.chat(
                new AgentChatRequest(message, sessionId),
                username
        );
        sendUnchecked(emitter, "token", Map.of("text", response.getReply()));
        sendUnchecked(emitter, "done", response);
    }

    private AgentChatResponse handleCrisis(AgentChatRequest request,
                                           String username,
                                           String message) {
        Long sessionId = conversationMemoryService.resolveSessionId(
                username,
                request.getSessionId(),
                message
        );
        String reply = crisisSupportService.crisisReply();
        var analysis = crisisWorkflowService.handle(username, message);
        conversationMemoryService.rememberAgentTurn(new AgentTurnPersistRequest(
                username,
                sessionId,
                message,
                reply,
                "",
                "",
                "",
                Map.of()
        ));
        return new AgentChatResponse(
                reply,
                false,
                null,
                sessionId,
                true,
                crisisSupportService.helpCenterUrl(),
                crisisSupportService.resources(),
                analysis,
                null
        );
    }

    private void streamCrisis(SseEmitter emitter,
                              AgentChatRequest request,
                              String username,
                              String message) {
        sendUnchecked(emitter, "status", Map.of(
                "node", "safety_guard",
                "label", "安全规则已触发，进入危机支持流程"
        ));
        AgentChatResponse response = handleCrisis(request, username, message);
        sendUnchecked(emitter, "token", Map.of("text", response.getReply()));
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
