package com.vanilo.psych.agent.entity;

import lombok.AllArgsConstructor;
import lombok.Data;
import lombok.NoArgsConstructor;

import java.time.LocalDateTime;

/** Audit row written whenever the deterministic safety rules route a message to crisis support. */
@Data
@AllArgsConstructor
@NoArgsConstructor
public class RiskAlertEvent {
    private Long id;
    private String username;
    private String message;
    private String risk;
    private String status;
    private LocalDateTime createdAt;
}
