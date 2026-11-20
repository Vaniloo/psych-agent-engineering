package com.vanilo.psych.agent.repository;

import com.vanilo.psych.agent.entity.ConversationMemory;
import com.vanilo.psych.agent.entity.User;
import java.util.Optional;
import org.apache.ibatis.annotations.*;

@Mapper
public interface ConversationMemoryMapper {
    @Select("SELECT * FROM conversation_memories WHERE user_id = #{user.id}")
    @Results(id = "row", value = {
        @Result(column = "user_id", property = "user.id")
    })
    Optional<ConversationMemory> findByUser(@Param("user") User user);

    @Insert("""
            INSERT INTO conversation_memories
                (user_id, summary, long_term_memory, updated_at)
            VALUES
                (#{user.id}, #{summary}, #{longTermMemory}, #{updatedAt})
            """)
    @Options(useGeneratedKeys = true, keyProperty = "id")
    void insert(ConversationMemory row);

    @Update("""
            UPDATE conversation_memories SET
                summary = #{summary}, long_term_memory = #{longTermMemory}, updated_at = #{updatedAt}
            WHERE id = #{id} AND user_id = #{user.id}
            """)
    int update(ConversationMemory row);
}
