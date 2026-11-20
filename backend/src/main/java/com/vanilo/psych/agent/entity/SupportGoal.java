package com.vanilo.psych.agent.entity;

import lombok.Data;
import lombok.NoArgsConstructor;

import java.time.LocalDateTime;

@Data
@NoArgsConstructor
public class SupportGoal {
    private Long id;
    private User user;
    private Long version;
    private String title;
    private String description = "";
    private String completionCriteria = "";
    private String status = "active";
    private String actionsJson = "[]";
    private LocalDateTime updatedAt;
}
