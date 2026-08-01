package com.vanilo.psych.agent.service;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.fasterxml.jackson.datatype.jsr310.JavaTimeModule;
import com.vanilo.psych.agent.dto.AgentChatResponse;
import com.vanilo.psych.agent.dto.AgentRuntimeRequest;
import org.junit.jupiter.api.Test;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertNotNull;

class AgentRuntimeContractTests {
    private final ObjectMapper objectMapper = new ObjectMapper().registerModule(new JavaTimeModule());

    @Test
    void serializesRuntimeRequestWithCamelCaseTransportFields() throws Exception {
        JsonNode json = objectMapper.readTree(objectMapper.writeValueAsString(
                new AgentRuntimeRequest("alice", 7L, "你好", "request-1")
        ));
        assertEquals(7L, json.path("sessionId").asLong());
        assertEquals("request-1", json.path("requestId").asText());
    }

    @Test
    void deserializesLangGraphResponseAndRagTrace() throws Exception {
        AgentChatResponse response = objectMapper.readValue("""
                {
                  "reply": "可以先做一次缓慢呼吸。",
                  "usedTool": true,
                  "toolName": "search_knowledge",
                  "sessionId": 7,
                  "crisis": false,
                  "resources": [],
                  "ragTrace": {
                    "requestId": "request-1",
                    "tool": "search_knowledge",
                    "query": "焦虑怎么办",
                    "category": "anxiety",
                    "citationCount": 1,
                    "status": "completed",
                    "executedAt": "2026-07-25T13:00:00",
                    "citations": [{
                      "rank": 1,
                      "id": "document-1",
                      "category": "anxiety",
                      "source": "handbook",
                      "relevanceScore": 0.82,
                      "confidenceLabel": "high",
                      "matchReason": "向量语义与关键词双路命中",
                      "excerpt": "先放慢呼吸。"
                    }]
                  }
                }
                """, AgentChatResponse.class);

        assertEquals(7L, response.getSessionId());
        assertNotNull(response.getRagTrace());
        assertEquals(1, response.getRagTrace().getCitations().size());
    }
}
