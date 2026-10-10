package com.vanilo.psych.agent.dto;
import jakarta.validation.constraints.*;
public record GoalReflectRequest(@NotNull @Positive Long checkInId,@NotNull @PositiveOrZero Long version) {}
