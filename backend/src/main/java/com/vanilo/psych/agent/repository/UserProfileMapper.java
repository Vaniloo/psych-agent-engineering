package com.vanilo.psych.agent.repository;

import com.vanilo.psych.agent.entity.User;
import com.vanilo.psych.agent.entity.UserProfile;
import java.util.Optional;
import org.apache.ibatis.annotations.*;

@Mapper
public interface UserProfileMapper {
    @Select("""
            SELECT t.*, u.username FROM user_profiles t LEFT JOIN users u ON u.id = t.user_id
            WHERE t.user_id = #{user.id}
            """)
    @Results(id = "row", value = {
        @Result(column = "user_id", property = "user.id"),
        @Result(column = "username", property = "user.username")
    })
    Optional<UserProfile> findByUser(@Param("user") User user);

    @Insert("""
            INSERT INTO user_profiles
                (user_id, profile_summary, concerns, preferences, coping_strategies, risk_signals,
                 updated_at)
            VALUES
                (#{user.id}, #{profileSummary}, #{concerns}, #{preferences}, #{copingStrategies},
                 #{riskSignals}, #{updatedAt})
            """)
    @Options(useGeneratedKeys = true, keyProperty = "id")
    void insert(UserProfile row);

    @Update("""
            UPDATE user_profiles SET
                profile_summary = #{profileSummary}, concerns = #{concerns}, preferences = #{preferences},
                coping_strategies = #{copingStrategies}, risk_signals = #{riskSignals},
                updated_at = #{updatedAt}
            WHERE id = #{id} AND user_id = #{user.id}
            """)
    int update(UserProfile row);
}
