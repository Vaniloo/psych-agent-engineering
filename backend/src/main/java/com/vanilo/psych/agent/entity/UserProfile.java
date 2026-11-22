package com.vanilo.psych.agent.entity;

import lombok.AllArgsConstructor;
import lombok.Data;
import lombok.NoArgsConstructor;

import java.time.LocalDateTime;

@Data
@AllArgsConstructor
@NoArgsConstructor
public class UserProfile {
    private Long id;
    private User user;
    private String profileSummary;
    private String concerns;
    private String preferences;
    private String copingStrategies;
    private String riskSignals;
    private LocalDateTime updatedAt;
}
