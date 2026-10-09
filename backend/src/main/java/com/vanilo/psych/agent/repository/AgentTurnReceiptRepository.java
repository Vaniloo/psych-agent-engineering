package com.vanilo.psych.agent.repository;

import com.vanilo.psych.agent.entity.AgentTurnReceipt;
import com.vanilo.psych.agent.entity.User;

import org.springframework.data.domain.Pageable;
import org.springframework.data.jpa.repository.JpaRepository;

import java.util.List;
import java.util.Optional;

public interface AgentTurnReceiptRepository extends JpaRepository<AgentTurnReceipt, Long> {
    Optional<AgentTurnReceipt> findByUserAndRequestId(User user, String requestId);

    List<AgentTurnReceipt> findByMemoryStatusOrderByIdAsc(String status, Pageable page);
}
