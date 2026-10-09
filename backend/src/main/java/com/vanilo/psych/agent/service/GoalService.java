package com.vanilo.psych.agent.service;

import com.fasterxml.jackson.core.type.TypeReference;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.vanilo.psych.agent.dto.GoalRequest;
import com.vanilo.psych.agent.entity.SupportGoal;
import com.vanilo.psych.agent.entity.User;
import com.vanilo.psych.agent.repository.*;

import org.springframework.http.HttpStatus;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;
import org.springframework.web.server.ResponseStatusException;

import java.time.LocalDateTime;
import java.util.*;

@Service
@Transactional
public class GoalService {
    private final SupportGoalRepository goals;
    private final UserRepository users;
    private final ObjectMapper json;

    public GoalService(SupportGoalRepository goals, UserRepository users, ObjectMapper json) {
        this.goals = goals;
        this.users = users;
        this.json = json;
    }

    private User user(String username) {
        return users.findByUsername(username)
                .orElseThrow(() -> new ResponseStatusException(HttpStatus.NOT_FOUND));
    }

    private SupportGoal owned(String username, Long id) {
        return goals.findByIdAndUser(id, user(username))
                .orElseThrow(() -> new ResponseStatusException(HttpStatus.NOT_FOUND));
    }

    @Transactional(readOnly = true)
    public List<Map<String, Object>> list(String username) {
        return goals.findTop50ByUserOrderByUpdatedAtDesc(user(username)).stream()
                .map(this::view)
                .toList();
    }

    public Map<String, Object> save(String username, Long id, GoalRequest request) {
        if (!request.confirmed())
            throw new ResponseStatusException(HttpStatus.BAD_REQUEST, "目标需用户明确确认");
        if (request.actions().stream().map(GoalRequest.Action::id).distinct().count()
                != request.actions().size())
            throw new ResponseStatusException(HttpStatus.BAD_REQUEST, "行动标识重复");
        SupportGoal goal = id == null ? new SupportGoal() : owned(username, id);
        if (id == null) goal.setUser(user(username));
        else if (request.version() == null || !request.version().equals(goal.getVersion()))
            throw new ResponseStatusException(HttpStatus.CONFLICT, "目标已更新，请刷新后再保存");
        goal.setTitle(request.title().strip());
        goal.setDescription(Objects.toString(request.description(), ""));
        goal.setStatus(request.status());
        goal.setUpdatedAt(LocalDateTime.now());
        try {
            goal.setActionsJson(json.writeValueAsString(request.actions()));
        } catch (Exception e) {
            throw new IllegalArgumentException("行动格式错误", e);
        }
        return view(goals.saveAndFlush(goal));
    }

    public void delete(String username, Long id) {
        goals.delete(owned(username, id));
    }

    private Map<String, Object> view(SupportGoal goal) {
        try {
            return Map.of(
                    "id",
                    goal.getId(),
                    "version",
                    goal.getVersion(),
                    "title",
                    goal.getTitle(),
                    "description",
                    goal.getDescription(),
                    "status",
                    goal.getStatus(),
                    "updatedAt",
                    goal.getUpdatedAt().toString(),
                    "actions",
                    json.readValue(
                            goal.getActionsJson(),
                            new TypeReference<List<GoalRequest.Action>>() {}));
        } catch (Exception e) {
            throw new IllegalStateException("目标记录读取失败", e);
        }
    }
}
