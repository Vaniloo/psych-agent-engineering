package com.vanilo.psych.agent.repository;

import com.vanilo.psych.agent.entity.ConversationMessage;
import com.vanilo.psych.agent.entity.ConversationSession;
import com.vanilo.psych.agent.entity.User;
import org.springframework.data.jpa.repository.JpaRepository;

import java.time.LocalDateTime;
import java.util.List;

public interface ConversationMessageRepository extends JpaRepository<ConversationMessage, Long> {
    List<ConversationMessage> findTop8ByUserAndRoleOrderByCreatedAtDesc(User user,String role);

    List<ConversationMessage> findTop12ByUserOrderByCreatedAtDesc(User user);

    List<ConversationMessage> findTop12ByUserAndSessionOrderByCreatedAtDesc(User user, ConversationSession session);

    List<ConversationMessage> findTop40ByUserAndSessionOrderByCreatedAtDesc(User user, ConversationSession session);

    List<ConversationMessage> findTop31ByUserAndSessionAndRoleAndCreatedAtGreaterThanEqualOrderByCreatedAtDesc(User user, ConversationSession session, String role, LocalDateTime since);

    List<ConversationMessage> findByUserAndSessionOrderByCreatedAtAsc(User user, ConversationSession session);
}
