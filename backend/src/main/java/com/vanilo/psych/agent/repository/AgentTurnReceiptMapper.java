package com.vanilo.psych.agent.repository;

import com.vanilo.psych.agent.entity.AgentTurnReceipt;
import com.vanilo.psych.agent.entity.User;
import java.time.LocalDateTime;
import java.util.List;
import java.util.Optional;
import org.apache.ibatis.annotations.*;

@Mapper
public interface AgentTurnReceiptMapper {
    @Select("""
            SELECT t.*, (SELECT u.username FROM users u WHERE u.id = t.user_id) AS username
            FROM agent_turn_receipts t
            WHERE t.id = #{id}
            """)
    @Results(id = "row", value = {
        @Result(column = "user_id", property = "user.id"),
        @Result(column = "username", property = "user.username")
    })
    Optional<AgentTurnReceipt> findById(Long id);

    @Select("SELECT COUNT(*) FROM agent_turn_receipts")
    long count();

    @Select("""
            SELECT t.*, (SELECT u.username FROM users u WHERE u.id = t.user_id) AS username
            FROM agent_turn_receipts t
            WHERE t.user_id = #{user.id} AND request_id = #{requestId}
            """)
    @ResultMap("row")
    Optional<AgentTurnReceipt> findByUserAndRequestId(User user, String requestId);

    @Select("""
            SELECT t.*, (SELECT u.username FROM users u WHERE u.id = t.user_id) AS username
            FROM agent_turn_receipts t
            WHERE t.user_id = #{user.id} AND request_id = #{requestId} FOR UPDATE
            """)
    @ResultMap("row")
    Optional<AgentTurnReceipt> findOwnedForGoalCreation(User user, String requestId);

    @Select("""
            SELECT t.*, (SELECT u.username FROM users u WHERE u.id = t.user_id) AS username
            FROM agent_turn_receipts t
            WHERE t.user_id = #{user.id} AND session_id = #{sessionId} ORDER BY id DESC LIMIT 1
            """)
    @ResultMap("row")
    Optional<AgentTurnReceipt> latestInSession(User user, Long sessionId);

    @Select("""
            SELECT t.*, (SELECT u.username FROM users u WHERE u.id = t.user_id) AS username
            FROM agent_turn_receipts t
            WHERE t.user_id = #{user.id} AND session_id = #{sessionId} AND id < #{id} ORDER BY id DESC LIMIT 1
            """)
    @ResultMap("row")
    Optional<AgentTurnReceipt> previousInSession(User user, Long sessionId, Long id);

    @Select("""
            SELECT t.*, (SELECT u.username FROM users u WHERE u.id = t.user_id) AS username
            FROM agent_turn_receipts t
            WHERE memory_status = #{status} ORDER BY id LIMIT #{limit}
            """)
    @ResultMap("row")
    List<AgentTurnReceipt> memoryJobs(String status, int limit);

    @Select("""
            SELECT COUNT(*) FROM agent_turn_receipts
            WHERE user_id = #{user.id} AND created_at >= #{since} AND created_goal_id IS NOT NULL
            """)
    long countCreatedGoals(User user, LocalDateTime since);

    @Update("UPDATE agent_turn_receipts SET memory_status = 'DONE' WHERE id = #{id}")
    int completeMemoryJob(Long id);

    @Insert("""
            INSERT INTO agent_turn_receipts
                (user_id, request_id, fingerprint, session_id, user_message, assistant_reply, memory_status,
                 created_goal_id, created_at)
            VALUES
                (#{user.id}, #{requestId}, #{fingerprint}, #{sessionId}, #{userMessage}, #{assistantReply},
                 #{memoryStatus}, #{createdGoalId}, #{createdAt})
            """)
    @Options(useGeneratedKeys = true, keyProperty = "id")
    void insert(AgentTurnReceipt row);

    @Update("""
            UPDATE agent_turn_receipts SET
                fingerprint = #{fingerprint}, user_message = #{userMessage}, assistant_reply =
                #{assistantReply}, memory_status = #{memoryStatus}, created_goal_id = #{createdGoalId}
            WHERE id = #{id} AND user_id = #{user.id}
            """)
    int update(AgentTurnReceipt row);

    default AgentTurnReceipt save(AgentTurnReceipt row) {
        if (row.getId() == null) insert(row);
        else update(row);
        return row;
    }
}
