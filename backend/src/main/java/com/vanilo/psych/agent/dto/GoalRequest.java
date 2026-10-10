package com.vanilo.psych.agent.dto;

import jakarta.validation.Valid;
import jakarta.validation.constraints.*;

import java.util.List;

public record GoalRequest(
        @NotBlank @Size(max = 200) String title,
        @Size(max = 1000) String description,
        @NotNull @Pattern(regexp = "active|paused|completed") String status,
        Long version,
        @AssertTrue(message = "请确认这些目标与反馈来自你的选择") boolean confirmed,
        @NotNull @Size(max = 12) List<@NotNull @Valid Action> actions,
        @Size(max=1000) String completionCriteria) {
    public GoalRequest(String title,String description,String status,Long version,boolean confirmed,List<Action> actions) {
        this(title,description,status,version,confirmed,actions,"");
    }
    public record Action(
            @NotBlank @Size(max = 80) String id,
            @NotBlank @Size(max = 300) String title,
            @NotNull @Pattern(regexp = "planned|done|skipped") String status,
            @NotNull @Size(max = 20) List<@NotBlank @Size(max = 1000) String> feedback) {}
}
