package com.vanilo.psych.agent.controller;

import com.vanilo.psych.agent.dto.GoalRequest;
import com.vanilo.psych.agent.service.GoalService;

import jakarta.validation.Valid;

import org.springframework.security.core.Authentication;
import org.springframework.web.bind.annotation.*;

import java.util.*;

@RestController
@RequestMapping("/goals")
public class GoalController {
    private final GoalService goals;

    public GoalController(GoalService goals) {
        this.goals = goals;
    }

    @GetMapping
    public List<Map<String, Object>> list(Authentication auth) {
        return goals.list(auth.getName());
    }

    @PostMapping
    public Map<String, Object> create(
            Authentication auth, @Valid @RequestBody GoalRequest request) {
        return goals.save(auth.getName(), null, request);
    }

    @PutMapping("/{id}")
    public Map<String, Object> update(
            Authentication auth, @PathVariable Long id, @Valid @RequestBody GoalRequest request) {
        return goals.save(auth.getName(), id, request);
    }

    @DeleteMapping("/{id}")
    public Map<String, Object> delete(Authentication auth, @PathVariable Long id) {
        goals.delete(auth.getName(), id);
        return Map.of("deleted", true);
    }
}
