package com.vanilo.psych.agent.repository;

import com.vanilo.psych.agent.entity.SupportGoal;
import com.vanilo.psych.agent.entity.User;
import java.util.List;
import java.util.Optional;
import org.apache.ibatis.annotations.Mapper;
import org.apache.ibatis.annotations.Param;
import org.springframework.dao.OptimisticLockingFailureException;

@Mapper
public interface SupportGoalMapper {
    Optional<SupportGoal> findByIdAndUser(@Param("id") Long id, @Param("user") User user);

    List<SupportGoal> listRecent(@Param("user") User user);

    List<SupportGoal> recentByStatus(@Param("user") User user, @Param("status") String status);

    List<SupportGoal> recentByTitle(@Param("user") User user, @Param("title") String title);

    void insert(SupportGoal row);

    int update(SupportGoal row);

    default SupportGoal save(SupportGoal goal) {
        if (goal.getId() == null) {
            goal.setVersion(0L);
            insert(goal);
        } else {
            requireCurrentVersion(update(goal), goal);
            goal.setVersion(goal.getVersion() + 1);
        }
        return goal;
    }

    default void delete(SupportGoal goal) {
        requireCurrentVersion(deleteVersion(goal), goal);
    }

    private static void requireCurrentVersion(int changed, SupportGoal goal) {
        if (changed != 1) {
            throw new OptimisticLockingFailureException("Stale goal version for id " + goal.getId());
        }
    }

    int deleteVersion(SupportGoal goal);
}
