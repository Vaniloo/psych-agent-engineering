package com.vanilo.psych.agent.entity;

import com.fasterxml.jackson.annotation.JsonIgnore;
import jakarta.persistence.*;
import lombok.Data;
import lombok.ToString;

@Entity
@Table(name = "user_model_api")
@Data
public class UserModelApi {
    @Id private Long userId;
    @Column(nullable = false, length = 500) private String baseUrl;
    @Column(nullable = false, length = 160) private String model;
    @Column(nullable = false, length = 8192) @JsonIgnore @ToString.Exclude private String encryptedKey;
}
