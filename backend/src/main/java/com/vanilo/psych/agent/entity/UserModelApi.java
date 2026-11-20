package com.vanilo.psych.agent.entity;

import com.fasterxml.jackson.annotation.JsonIgnore;
import lombok.Data;
import lombok.ToString;

@Data
public class UserModelApi {
    private Long userId;
    private String baseUrl;
    private String model;
    @JsonIgnore @ToString.Exclude private String encryptedKey;
}
