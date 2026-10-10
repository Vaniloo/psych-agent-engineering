package com.vanilo.psych.agent.dto;
import jakarta.validation.constraints.*;
public record GoalCheckInRequest(@NotBlank @Size(max=80) String actionId,
        @NotNull @Pattern(regexp="partial|done|blocked|rest") String outcome,
        @Size(max=500) String note, @NotNull Long version,
        @AssertTrue(message="请确认这次记录来自你的选择") boolean confirmed) {}
