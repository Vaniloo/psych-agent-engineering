package com.vanilo.psych.agent.repository;

import com.vanilo.psych.agent.entity.*;

import org.springframework.data.jpa.repository.JpaRepository;

import java.util.List;
import java.util.Optional;

public interface SupportGoalRepository extends JpaRepository<SupportGoal, Long> {
    List<SupportGoal> findTop50ByUserOrderByUpdatedAtDesc(User user);

    Optional<SupportGoal> findByIdAndUser(Long id, User user);
}
