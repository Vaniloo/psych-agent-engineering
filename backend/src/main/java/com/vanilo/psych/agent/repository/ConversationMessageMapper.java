package com.vanilo.psych.agent.repository;

import com.vanilo.psych.agent.entity.ConversationMessage;
import com.vanilo.psych.agent.entity.ConversationSession;
import com.vanilo.psych.agent.entity.User;
import java.time.LocalDateTime;
import java.util.List;
import org.apache.ibatis.annotations.Mapper;
import org.apache.ibatis.annotations.Param;

@Mapper
public interface ConversationMessageMapper {
    List<ConversationMessage> recentByRole(
            @Param("user") User user,
            @Param("role") String role,
            @Param("limit") int limit);

    List<ConversationMessage> recentInSession(
            @Param("user") User user,
            @Param("session") ConversationSession session,
            @Param("limit") int limit);

    List<ConversationMessage> recentEvidence(
            @Param("user") User user,
            @Param("session") ConversationSession session,
            @Param("role") String role,
            @Param("since") LocalDateTime since);

    List<ConversationMessage> sessionHistory(
            @Param("user") User user,
            @Param("session") ConversationSession session);

    void insert(ConversationMessage row);
}
