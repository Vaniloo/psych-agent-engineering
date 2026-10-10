package com.vanilo.psych.agent.controller;

import com.vanilo.psych.agent.dto.GoalRequest;
import com.vanilo.psych.agent.dto.GoalCheckInRequest;
import com.vanilo.psych.agent.dto.GoalCheckInResponse;
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

    @GetMapping("/check-ins")
    public List<GoalCheckInResponse> checkIns(Authentication auth) {
        return goals.checkIns(auth.getName());
    }
    @GetMapping("/preferences")
    public Map<String,Object> preferences(Authentication auth) {return goals.preferences(auth.getName());}
    @PutMapping("/preferences")
    public Map<String,Object> preferences(Authentication auth,@RequestBody Map<String,Boolean> body) {
        if (body.get("autoSync")==null) throw new org.springframework.web.server.ResponseStatusException(org.springframework.http.HttpStatus.BAD_REQUEST);
        return goals.setAutoSync(auth.getName(),body.get("autoSync"));
    }
    @PostMapping("/check-ins/{id}/undo")
    public Map<String,Object> undo(Authentication auth,@PathVariable Long id) {return goals.undoSync(auth.getName(),id);}
    @PostMapping("/{id}/check-ins")
    public Map<String,Object> checkIn(Authentication auth,@PathVariable Long id,
            @Valid @RequestBody GoalCheckInRequest request) {
        return goals.checkIn(auth.getName(),id,request);
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
