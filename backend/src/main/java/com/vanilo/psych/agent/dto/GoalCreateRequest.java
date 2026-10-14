package com.vanilo.psych.agent.dto;
import jakarta.validation.constraints.*;
public record GoalCreateRequest(@NotBlank @Size(max=128) String username,
        @NotBlank @Size(max=128) String requestId,@NotNull @Positive Long sessionId,
        @Size(min=1,max=200) String sourceGoalTitle) {
    public GoalCreateRequest(String username,String requestId,Long sessionId) {this(username,requestId,sessionId,null);}
}
