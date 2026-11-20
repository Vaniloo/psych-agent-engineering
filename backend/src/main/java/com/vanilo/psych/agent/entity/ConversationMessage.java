package com.vanilo.psych.agent.entity;

import lombok.AllArgsConstructor;
import lombok.Data;
import lombok.NoArgsConstructor;

import java.time.LocalDateTime;

@Data
@AllArgsConstructor
@NoArgsConstructor
public class ConversationMessage {
    private Long id;
    private User user;
    private ConversationSession session;
    private String role;
    private String content;
    private LocalDateTime createdAt;
}
