package com.vanilo.psych.agent.dto;
import java.time.LocalDateTime;
public record GoalCheckInResponse(Long id,Long goalId,String goalTitle,String actionId,String actionTitle,
        String outcome,String note,LocalDateTime createdAt,String source,boolean undone) {}
