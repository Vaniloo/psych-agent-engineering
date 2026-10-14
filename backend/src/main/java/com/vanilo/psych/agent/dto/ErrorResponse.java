package com.vanilo.psych.agent.dto;

import lombok.AllArgsConstructor;
import lombok.Data;
import lombok.NoArgsConstructor;

@Data
@AllArgsConstructor
@NoArgsConstructor
public class ErrorResponse {
    private Boolean success;
    private String message;
    @com.fasterxml.jackson.annotation.JsonInclude(com.fasterxml.jackson.annotation.JsonInclude.Include.NON_NULL)
    private String code;

    public ErrorResponse(Boolean success, String message) {
        this.success = success;
        this.message = message;
    }
}
