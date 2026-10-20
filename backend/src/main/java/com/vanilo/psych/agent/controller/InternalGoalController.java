package com.vanilo.psych.agent.controller;

import com.vanilo.psych.agent.security.InternalServiceAuthenticator;
import com.vanilo.psych.agent.service.GoalService;
import com.vanilo.psych.agent.service.GoalCreationService;
import com.vanilo.psych.agent.dto.GoalCreateRequest;
import com.vanilo.psych.agent.dto.GoalSyncRequest;
import com.vanilo.psych.agent.dto.GoalAdvanceRequest;
import com.vanilo.psych.agent.service.GoalCoachService;
import jakarta.validation.Valid;

import org.springframework.web.bind.annotation.*;

import java.util.*;

@RestController
@RequestMapping("/internal/agent/goals")
public class InternalGoalController {
    private final GoalService goals;
    private final GoalCreationService creation;
    private final InternalServiceAuthenticator auth;
    private final GoalCoachService coach;

    public InternalGoalController(GoalService goals, InternalServiceAuthenticator auth, GoalCreationService creation, GoalCoachService coach) {
        this.goals = goals;
        this.creation = creation;
        this.auth = auth;
        this.coach = coach;
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

    @PostMapping("/manage")
    public Map<String,Object> manage(@RequestHeader("X-Internal-Service-Token") String token,
            @Valid @RequestBody com.vanilo.psych.agent.dto.GoalManageRequest request) {
        auth.verify(token);return goals.manageFromTurn(request);
    }

    @PostMapping("/advance")
    public Map<String,Object> advance(@RequestHeader("X-Internal-Service-Token") String token,
            @Valid @RequestBody GoalAdvanceRequest request) {
        auth.verify(token);return coach.advanceFromTurn(request);
    }

    @GetMapping
    public Map<String, Object> list(
            @RequestHeader("X-Internal-Service-Token") String token,
            @RequestParam String username, @RequestParam(required=false) String title,
            @RequestParam(required=false) String status, @RequestParam(required=false) Long sessionId,
            @RequestParam(required=false) String requestId) {
        auth.verify(token);
        Map<String,Object> result=new LinkedHashMap<>();
        var selected=title!=null?goals.named(username,title):status!=null?goals.listByStatus(username,status):goals.list(username);
        result.put("goals",selected);
        var selectedIds=selected.stream().map(g->g.get("id")).collect(java.util.stream.Collectors.toSet());
        // A planned step may already have partial/blocked/rest records. Its status
        // alone cannot establish whether the user's progress was saved.
        result.put("recentProgress",selectedIds.isEmpty()?List.of():goals.checkIns(username).stream()
                .filter(entry->!entry.undone()&&selectedIds.contains(entry.goalId())).limit(8).toList());
        result.put("autoSync",goals.preferences(username).get("autoSync"));
        if(sessionId!=null)result.put("conversationGoalId",goals.conversationGoalId(username,sessionId,requestId));
        return result;
    }
}
