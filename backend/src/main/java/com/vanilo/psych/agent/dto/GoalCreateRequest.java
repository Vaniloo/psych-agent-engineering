package com.vanilo.psych.agent.dto;
import jakarta.validation.constraints.*;
public record GoalCreateRequest(@NotBlank @Size(max=128) String username,
        @NotBlank @Size(max=128) String requestId,@NotNull @Positive Long sessionId,
        @Size(min=1,max=200) String sourceGoalTitle,@Size(max=2048) String modelApproval) {
    public GoalCreateRequest(String username,String requestId,Long sessionId) {this(username,requestId,sessionId,null,null);}
    public GoalCreateRequest(String username,String requestId,Long sessionId,String sourceGoalTitle) {
        this(username,requestId,sessionId,sourceGoalTitle,null);
    }
}
