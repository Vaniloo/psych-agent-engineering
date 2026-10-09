package com.vanilo.psych.agent.service;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.vanilo.psych.agent.config.AgentRuntimeProperties;
import com.vanilo.psych.agent.dto.*;
import com.vanilo.psych.agent.exception.ServiceUnavailableException;

import org.springframework.stereotype.Service;

import java.io.IOException;
import java.net.URI;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.time.Duration;
import java.util.List;
import java.util.function.BiConsumer;

@Service
public class AgentRuntimeClient {
    private final AgentRuntimeProperties properties;
    private final ObjectMapper objectMapper;
    private final HttpClient httpClient;

    public AgentRuntimeClient(AgentRuntimeProperties properties, ObjectMapper objectMapper) {
        this.properties = properties;
        this.objectMapper = objectMapper;
        this.httpClient =
                HttpClient.newBuilder()
                        .connectTimeout(Duration.ofMillis(properties.getConnectTimeoutMs()))
                        .version(HttpClient.Version.HTTP_1_1)
                        .build();
    }

    public AgentChatResponse chat(AgentRuntimeRequest request) {
        try {
            HttpResponse<String> response = sendJson("/v1/agent/chat", request);
            requireSuccess(response);
            return objectMapper.readValue(response.body(), AgentChatResponse.class);
        } catch (IOException | InterruptedException exception) {
            if (exception instanceof InterruptedException) {
                Thread.currentThread().interrupt();
            }
            throw new ServiceUnavailableException("LangGraph Agent Runtime 暂时不可用", exception);
        }
    }

    public void stream(AgentRuntimeRequest request, BiConsumer<String, JsonNode> eventConsumer) {
        try {
            HttpRequest httpRequest = jsonRequest("/v1/agent/stream", request);
            HttpResponse<java.util.stream.Stream<String>> response =
                    httpClient.send(httpRequest, HttpResponse.BodyHandlers.ofLines());
            if (response.statusCode() < 200 || response.statusCode() >= 300) {
                throw new ServiceUnavailableException(
                        "Agent Runtime SSE 返回 " + response.statusCode());
            }
            String[] eventName = {"message"};
            StringBuilder data = new StringBuilder();
            response.body()
                    .forEach(
                            line -> {
                                if (line.isBlank()) {
                                    dispatchEvent(eventName[0], data, eventConsumer);
                                    eventName[0] = "message";
                                    data.setLength(0);
                                    return;
                                }
                                if (line.startsWith("event:")) {
                                    eventName[0] = line.substring(6).strip();
                                } else if (line.startsWith("data:")) {
                                    if (!data.isEmpty()) {
                                        data.append('\n');
                                    }
                                    data.append(line.substring(5).strip());
                                }
                            });
            dispatchEvent(eventName[0], data, eventConsumer);
        } catch (IOException | InterruptedException exception) {
            if (exception instanceof InterruptedException) {
                Thread.currentThread().interrupt();
            }
            throw new ServiceUnavailableException("Agent Runtime SSE 连接失败", exception);
        }
    }

    public JsonNode runtimeInfo() {
        try {
            var request=HttpRequest.newBuilder().uri(URI.create(properties.getBaseUrl().replaceAll("/$", "")+"/v1/runtime/info"))
                    .timeout(Duration.ofSeconds(5)).header("X-Internal-Service-Token",properties.getInternalServiceToken()).GET().build();
            var response=httpClient.send(request,HttpResponse.BodyHandlers.ofString());requireSuccess(response);
            return objectMapper.readTree(response.body());
        } catch(IOException|InterruptedException e) {
            if(e instanceof InterruptedException)Thread.currentThread().interrupt();
            throw new ServiceUnavailableException("运行状态暂不可用",e);
        }
    }

    public int upsertKnowledge(KnowledgeRuntimeUpsertRequest request) {
        try {
            HttpResponse<String> response = sendJson("/v1/knowledge/upsert", request);
            requireSuccess(response);
            return objectMapper.readTree(response.body()).path("chunkCount").asInt();
        } catch (IOException | InterruptedException exception) {
            if (exception instanceof InterruptedException) {
                Thread.currentThread().interrupt();
            }
            throw new ServiceUnavailableException("知识索引服务暂时不可用", exception);
        }
    }

    public void deleteKnowledge(String documentId) {
        try {
            HttpResponse<String> response =
                    sendJson("/v1/knowledge/delete", java.util.Map.of("document_id", documentId));
            requireSuccess(response);
        } catch (IOException | InterruptedException exception) {
            if (exception instanceof InterruptedException) {
                Thread.currentThread().interrupt();
            }
            throw new ServiceUnavailableException("知识索引删除失败", exception);
        }
    }

    public List<KnowledgeSearchResponse> searchKnowledge(String query, String category, int limit) {
        try {
            HttpResponse<String> response =
                    sendJson(
                            "/v1/knowledge/search",
                            java.util.Map.of(
                                    "query", query,
                                    "category", category == null ? "" : category,
                                    "limit", limit));
            requireSuccess(response);
            RuntimeKnowledgeSearchResponse result =
                    objectMapper.readValue(response.body(), RuntimeKnowledgeSearchResponse.class);
            if (result.getResults() == null) {
                return List.of();
            }
            return result.getResults().stream()
                    .map(RuntimeKnowledgeSearchItem::toResponse)
                    .toList();
        } catch (IOException | InterruptedException exception) {
            if (exception instanceof InterruptedException) {
                Thread.currentThread().interrupt();
            }
            throw new ServiceUnavailableException("知识检索服务暂时不可用", exception);
        }
    }

    public JsonNode evaluateRag() {
        try {
            HttpResponse<String> response = sendJson("/v1/evaluation/rag", java.util.Map.of());
            requireSuccess(response);
            return objectMapper.readTree(response.body());
        } catch (IOException | InterruptedException exception) {
            if (exception instanceof InterruptedException) {
                Thread.currentThread().interrupt();
            }
            throw new ServiceUnavailableException("RAG 离线评测服务暂时不可用", exception);
        }
    }

    private HttpResponse<String> sendJson(String path, Object body)
            throws IOException, InterruptedException {
        return httpClient.send(jsonRequest(path, body), HttpResponse.BodyHandlers.ofString());
    }

    private HttpRequest jsonRequest(String path, Object body) throws IOException {
        String requestId =
                body instanceof AgentRuntimeRequest runtimeRequest
                        ? runtimeRequest.getRequestId()
                        : java.util.UUID.randomUUID().toString();
        return HttpRequest.newBuilder()
                .uri(URI.create(properties.getBaseUrl().replaceAll("/$", "") + path))
                .timeout(Duration.ofMillis(properties.getRequestTimeoutMs()))
                .header("Content-Type", "application/json")
                .header("Accept", "application/json, text/event-stream")
                .header("X-Request-ID", requestId)
                .header("X-Internal-Service-Token", properties.getInternalServiceToken())
                .POST(HttpRequest.BodyPublishers.ofString(objectMapper.writeValueAsString(body)))
                .build();
    }

    private void requireSuccess(HttpResponse<String> response) {
        if (response.statusCode() < 200 || response.statusCode() >= 300) {
            throw new ServiceUnavailableException(
                    "Agent Runtime 返回 " + response.statusCode() + ": " + response.body());
        }
    }

    private void dispatchEvent(
            String event, StringBuilder data, BiConsumer<String, JsonNode> consumer) {
        if (data.isEmpty()) {
            return;
        }
        try {
            consumer.accept(event, objectMapper.readTree(data.toString()));
        } catch (IOException exception) {
            throw new ServiceUnavailableException("无法解析 Agent Runtime SSE 事件", exception);
        }
    }
}
