package com.vanilo.psych.agent.repository;

import com.vanilo.psych.agent.entity.ConversationSession;
import com.vanilo.psych.agent.entity.User;
import org.springframework.data.jpa.repository.JpaRepository;

import java.time.LocalDateTime;
import java.util.List;
import java.util.Optional;

public interface ConversationSessionRepository extends JpaRepository<ConversationSession, Long> {
    List<ConversationSession> findByUserOrderByUpdatedAtDesc(User user);

    List<ConversationSession> findTop8ByUserAndUpdatedAtGreaterThanEqualOrderByUpdatedAtDesc(User user, LocalDateTime since);

    Optional<ConversationSession> findByIdAndUser(Long id, User user);
}
