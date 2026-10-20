package com.vanilo.psych.agent.dto;
import jakarta.validation.constraints.*;
public record GoalManageRequest(@NotBlank String username,@NotBlank @Size(max=128) String requestId,
        @NotNull Long sessionId,@NotNull Long goalId,@NotNull Long version,
        @NotBlank @Pattern(regexp="revise|complete|pause") String operation,
        @NotBlank @Size(max=6000) String editsJson,@NotBlank @Size(max=2048) String modelApproval) {}
