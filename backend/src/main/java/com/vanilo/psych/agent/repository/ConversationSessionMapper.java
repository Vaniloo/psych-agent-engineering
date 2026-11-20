package com.vanilo.psych.agent.repository;

import com.vanilo.psych.agent.entity.ConversationSession;
import com.vanilo.psych.agent.entity.User;
import java.time.LocalDateTime;
import java.util.List;
import java.util.Optional;
import org.apache.ibatis.annotations.Mapper;
import org.apache.ibatis.annotations.Param;

@Mapper
public interface ConversationSessionMapper {
    Optional<ConversationSession> findByIdAndUser(@Param("id") Long id, @Param("user") User user);

    List<ConversationSession> listOwned(@Param("user") User user);

    List<ConversationSession> recentSince(@Param("user") User user, @Param("since") LocalDateTime since);

    void insert(ConversationSession row);

    int update(ConversationSession row);
}
