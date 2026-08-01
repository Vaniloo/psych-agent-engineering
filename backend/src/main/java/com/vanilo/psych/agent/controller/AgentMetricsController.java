package com.vanilo.psych.agent.controller;

import com.vanilo.psych.agent.service.AgentMetricsService;
import org.springframework.security.core.Authentication;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;

import java.util.List;
import java.util.Map;

@RestController
@RequestMapping("/agent/metrics")
public class AgentMetricsController {
    private final AgentMetricsService metricsService;

    public AgentMetricsController(AgentMetricsService metricsService) {
        this.metricsService = metricsService;
    }

    @GetMapping("/summary")
    public Map<String, Object> summary(
            Authentication authentication,
            @RequestParam(defaultValue = "7") int days
    ) {
        return metricsService.summary(requireUsername(authentication), days);
    }

    @GetMapping("/recent")
    public List<Map<String, Object>> recent(
            Authentication authentication,
            @RequestParam(defaultValue = "20") int limit
    ) {
        return metricsService.recent(requireUsername(authentication), limit);
    }

    private String requireUsername(Authentication authentication) {
        if (authentication == null) {
            throw new RuntimeException("未登录");
        }
        return authentication.getName();
    }
}
