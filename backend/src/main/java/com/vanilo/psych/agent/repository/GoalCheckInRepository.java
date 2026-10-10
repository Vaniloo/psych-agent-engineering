package com.vanilo.psych.agent.repository;
import com.vanilo.psych.agent.entity.*;
import org.springframework.data.jpa.repository.JpaRepository;
import java.util.List;
import java.util.Optional;
public interface GoalCheckInRepository extends JpaRepository<GoalCheckIn,Long> {
    List<GoalCheckIn> findTop100ByUserOrderByCreatedAtDesc(User user);
    Optional<GoalCheckIn> findByIdAndUser(Long id,User user);
    Optional<GoalCheckIn> findByUserAndSourceRequestIdAndActionId(User user,String requestId,String actionId);
    List<GoalCheckIn> findTop12ByUserAndGoalAndUndoneFalseOrderByCreatedAtDesc(User user,SupportGoal goal);
    List<GoalCheckIn> findTop1001ByUserAndCreatedAtGreaterThanEqualAndCreatedAtLessThanOrderByCreatedAtAsc(User user,java.time.LocalDateTime start,java.time.LocalDateTime end);
    void deleteByGoal(SupportGoal goal);
}
