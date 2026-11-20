package com.vanilo.psych.agent.entity;

import lombok.Data;
import lombok.NoArgsConstructor;
import java.time.LocalDateTime;

@Data @NoArgsConstructor
public class GoalCheckIn {
    private Long id;
    private User user;
    private SupportGoal goal;
    private String actionId;
    private String actionTitle;
    private String outcome;
    private String note="";
    private String source="manual";
    private String sourceRequestId;
    private Long sessionId;
    private String coachingJson;
    private String previousStatus;
    private Long appliedGoalVersion;
    private boolean feedbackAdded;
    private boolean undone;
    private LocalDateTime createdAt;
}
