package com.vanilo.psych.agent.entity;

import lombok.AllArgsConstructor;
import lombok.Data;
import lombok.NoArgsConstructor;

import java.time.LocalDateTime;

@Data
@AllArgsConstructor
@NoArgsConstructor
public class RoleCard {
    private Long id;
    private User owner;
    private String name;
    private String description;
    private String tone;
    private String responseStyle;
    private String customInstructions;
    private String forbiddenExpressions;
    private String studioConfigJson;
    private String sourceCardJson;
    private byte[] avatarPng;
    private Boolean experimental;
    private boolean preset;
    private LocalDateTime createdAt;
}
