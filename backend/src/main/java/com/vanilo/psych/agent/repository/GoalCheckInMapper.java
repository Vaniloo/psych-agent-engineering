package com.vanilo.psych.agent.repository;

import com.vanilo.psych.agent.entity.GoalCheckIn;
import com.vanilo.psych.agent.entity.SupportGoal;
import com.vanilo.psych.agent.entity.User;
import java.time.LocalDateTime;
import java.util.List;
import java.util.Optional;
import org.apache.ibatis.annotations.Mapper;
import org.apache.ibatis.annotations.Param;

@Mapper
public interface GoalCheckInMapper {
    Optional<GoalCheckIn> findByIdAndUser(@Param("id") Long id, @Param("user") User user);

    Optional<GoalCheckIn> findOwnedForCoaching(@Param("id") Long id, @Param("user") User user);

    List<GoalCheckIn> listRecent(@Param("user") User user);

    Optional<GoalCheckIn> findRequestAction(
            @Param("user") User user,
            @Param("requestId") String requestId,
            @Param("actionId") String actionId);

    List<GoalCheckIn> findByRequest(@Param("user") User user, @Param("requestId") String requestId);

    List<GoalCheckIn> recentForGoal(@Param("user") User user, @Param("goal") SupportGoal goal);

    List<GoalCheckIn> inDateRange(
            @Param("user") User user,
            @Param("start") LocalDateTime start,
            @Param("end") LocalDateTime end);

    void insert(GoalCheckIn row);

    int update(GoalCheckIn row);

    void deleteByGoal(SupportGoal goal);
}
