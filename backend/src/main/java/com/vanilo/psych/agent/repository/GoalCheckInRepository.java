package com.vanilo.psych.agent.repository;
import com.vanilo.psych.agent.entity.*;
import org.springframework.data.jpa.repository.JpaRepository;
import java.util.List;
import java.util.Optional;
public interface GoalCheckInRepository extends JpaRepository<GoalCheckIn,Long> {
    @org.springframework.data.jpa.repository.Lock(jakarta.persistence.LockModeType.PESSIMISTIC_WRITE)
    @org.springframework.data.jpa.repository.Query("select e from GoalCheckIn e where e.id = :id and e.user = :user")
    Optional<GoalCheckIn> findOwnedForCoaching(@org.springframework.data.repository.query.Param("id") Long id,
            @org.springframework.data.repository.query.Param("user") User user);
    List<GoalCheckIn> findTop100ByUserOrderByCreatedAtDesc(User user);
    Optional<GoalCheckIn> findByIdAndUser(Long id,User user);
    Optional<GoalCheckIn> findByUserAndSourceRequestIdAndActionId(User user,String requestId,String actionId);
    List<GoalCheckIn> findByUserAndSourceRequestId(User user,String requestId);
    List<GoalCheckIn> findTop12ByUserAndGoalAndUndoneFalseOrderByCreatedAtDesc(User user,SupportGoal goal);
    List<GoalCheckIn> findTop1001ByUserAndCreatedAtGreaterThanEqualAndCreatedAtLessThanOrderByCreatedAtAsc(User user,java.time.LocalDateTime start,java.time.LocalDateTime end);
    void deleteByGoal(SupportGoal goal);
}
