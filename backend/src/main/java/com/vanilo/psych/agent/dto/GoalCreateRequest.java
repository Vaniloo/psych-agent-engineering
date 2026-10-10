package com.vanilo.psych.agent.dto;
import jakarta.validation.constraints.*;
public record GoalCreateRequest(@NotBlank @Size(max=128) String username,
        @NotBlank @Size(max=128) String requestId,@NotNull @Positive Long sessionId) {}
