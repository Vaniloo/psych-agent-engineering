package com.vanilo.psych.agent.repository;

import com.vanilo.psych.agent.entity.AgentRunMetric;
import org.springframework.data.domain.Pageable;
import org.springframework.data.jpa.repository.JpaRepository;

import java.time.LocalDateTime;
import java.util.List;

public interface AgentRunMetricRepository extends JpaRepository<AgentRunMetric, Long> {
    boolean existsByRequestId(String requestId);

    List<AgentRunMetric> findByUsernameAndStartedAtAfterOrderByStartedAtAsc(
            String username,
            LocalDateTime startedAt
    );

    List<AgentRunMetric> findByUsernameAndStartedAtAfterOrderByStartedAtDesc(String username,LocalDateTime since,Pageable page);

    List<AgentRunMetric> findByUsernameOrderByStartedAtDesc(
            String username,
            Pageable pageable
    );
}
