package com.vanilo.psych.agent.repository;

import com.vanilo.psych.agent.entity.AgentTurnReceipt;
import com.vanilo.psych.agent.entity.User;

import org.springframework.data.domain.Pageable;
import org.springframework.data.jpa.repository.JpaRepository;

import java.util.List;
import java.util.Optional;

public interface AgentTurnReceiptRepository extends JpaRepository<AgentTurnReceipt, Long> {
    Optional<AgentTurnReceipt> findByUserAndRequestId(User user, String requestId);

    @org.springframework.data.jpa.repository.Lock(jakarta.persistence.LockModeType.PESSIMISTIC_WRITE)
    @org.springframework.data.jpa.repository.Query("select r from AgentTurnReceipt r where r.user = :user and r.requestId = :requestId")
    Optional<AgentTurnReceipt> findOwnedForGoalCreation(@org.springframework.data.repository.query.Param("user") User user,
            @org.springframework.data.repository.query.Param("requestId") String requestId);

    long countByUserAndCreatedAtGreaterThanEqualAndCreatedGoalIdIsNotNull(User user,java.time.LocalDateTime since);

    List<AgentTurnReceipt> findByMemoryStatusOrderByIdAsc(String status, Pageable page);
}
