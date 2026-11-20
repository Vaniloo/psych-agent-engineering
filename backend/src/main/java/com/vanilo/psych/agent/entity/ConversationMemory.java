package com.vanilo.psych.agent.entity;

import lombok.AllArgsConstructor;
import lombok.Data;
import lombok.NoArgsConstructor;

import java.time.LocalDateTime;

@Data
@AllArgsConstructor
@NoArgsConstructor
public class ConversationMemory {
    private Long id;
    private User user;
    private String summary;
    private String longTermMemory;
    private LocalDateTime updatedAt;
}
