package com.vanilo.psych.agent.controller;

import com.vanilo.psych.agent.security.InternalServiceAuthenticator;
import com.vanilo.psych.agent.service.GoalService;
import com.vanilo.psych.agent.service.GoalCreationService;
import com.vanilo.psych.agent.dto.GoalCreateRequest;
import com.vanilo.psych.agent.dto.GoalSyncRequest;
import jakarta.validation.Valid;

import org.springframework.web.bind.annotation.*;

import java.util.*;

@RestController
@RequestMapping("/internal/agent/goals")
public class InternalGoalController {
    private final GoalService goals;
    private final GoalCreationService creation;
    private final InternalServiceAuthenticator auth;

    public InternalGoalController(GoalService goals, InternalServiceAuthenticator auth, GoalCreationService creation) {
        this.goals = goals;
        this.creation = creation;
        this.auth = auth;
    }

    @PostMapping("/create")
    public Map<String,Object> create(@RequestHeader("X-Internal-Service-Token") String token,
            @Valid @RequestBody GoalCreateRequest request) {
        auth.verify(token);return creation.createFromTurn(request);
    }

    @PostMapping("/sync")
    public Map<String,Object> sync(@RequestHeader("X-Internal-Service-Token") String token,
            @Valid @RequestBody GoalSyncRequest request) {
        auth.verify(token);return goals.syncFromTurn(request);
    }

    @GetMapping
    public Map<String, Object> list(
            @RequestHeader("X-Internal-Service-Token") String token,
            @RequestParam String username, @RequestParam(required=false) String title,
            @RequestParam(required=false) String status) {
        auth.verify(token);
        return Map.of("goals", title!=null?goals.named(username,title):status!=null?goals.listByStatus(username,status):goals.list(username),"autoSync",goals.preferences(username).get("autoSync"));
    }
}
