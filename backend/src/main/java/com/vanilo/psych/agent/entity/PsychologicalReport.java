package com.vanilo.psych.agent.entity;

import lombok.AllArgsConstructor;
import lombok.Data;
import lombok.NoArgsConstructor;

import java.time.LocalDateTime;

@Data
@AllArgsConstructor
@NoArgsConstructor
public class PsychologicalReport {
    private Long id;
    private String message;
    private String risk;
    private String emotion;
    private Double confidence;
    private LocalDateTime createdAt;
    private User user;

}
