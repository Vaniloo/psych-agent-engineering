package com.vanilo.psych.agent.controller;

import com.fasterxml.jackson.databind.JsonNode;
import com.vanilo.psych.agent.service.AgentMetricsService;
import com.vanilo.psych.agent.service.AgentRuntimeClient;
import org.springframework.web.bind.annotation.*;

import java.util.Map;

@RestController
@RequestMapping("/admin/agent-metrics")
public class AdminAgentMetricsController {
    private final AgentRuntimeClient runtimeClient;
    private final AgentMetricsService metricsService;

    public AdminAgentMetricsController(AgentRuntimeClient runtimeClient,
                                       AgentMetricsService metricsService) {
        this.runtimeClient = runtimeClient;
        this.metricsService = metricsService;
    }

    @GetMapping("/rag-evaluation/latest")
    public Map<String, Object> latestEvaluation() {
        return metricsService.latestEvaluation();
    }

    @PostMapping("/rag-evaluation")
    public JsonNode runEvaluation() {
        return metricsService.recordEvaluation(runtimeClient.evaluateRag());
    }
}
