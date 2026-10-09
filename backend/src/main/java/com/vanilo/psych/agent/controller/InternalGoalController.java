package com.vanilo.psych.agent.controller;

import com.vanilo.psych.agent.security.InternalServiceAuthenticator;
import com.vanilo.psych.agent.service.GoalService;

import org.springframework.web.bind.annotation.*;

import java.util.*;

@RestController
@RequestMapping("/internal/agent/goals")
public class InternalGoalController {
    private final GoalService goals;
    private final InternalServiceAuthenticator auth;

    public InternalGoalController(GoalService goals, InternalServiceAuthenticator auth) {
        this.goals = goals;
        this.auth = auth;
    }

    @GetMapping
    public Map<String, Object> list(
            @RequestHeader("X-Internal-Service-Token") String token,
            @RequestParam String username) {
        auth.verify(token);
        return Map.of("goals", goals.list(username));
    }
}
