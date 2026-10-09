package com.vanilo.psych.agent.dto;
import jakarta.validation.constraints.*;
public record GoalSyncRequest(@NotBlank String username,@NotBlank @Size(max=128) String requestId,
        @NotNull Long sessionId,@NotNull Long goalId,@NotBlank @Size(max=80) String actionId,
        @NotNull Long version,@NotNull @Pattern(regexp="partial|done|blocked|rest") String outcome,
        @NotBlank @Size(max=500) String quote) {}
