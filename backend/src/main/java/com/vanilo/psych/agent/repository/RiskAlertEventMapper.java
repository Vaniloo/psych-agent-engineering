package com.vanilo.psych.agent.repository;

import com.vanilo.psych.agent.entity.RiskAlertEvent;
import java.util.List;
import java.util.Optional;
import org.apache.ibatis.annotations.*;

@Mapper
public interface RiskAlertEventMapper {
    @Select("SELECT * FROM risk_alert_events WHERE id = #{id}")
    Optional<RiskAlertEvent> findById(Long id);

    @Select("""
            SELECT * FROM risk_alert_events
            WHERE status = #{status} AND attempts < #{attempts} ORDER BY created_at, id LIMIT 20
            """)
    List<RiskAlertEvent> retryable(String status, int attempts);

    @Insert("""
            INSERT INTO risk_alert_events
                (report_id, username, message, risk, emotion, confidence, status, attempts, last_error,
                 created_at, updated_at)
            VALUES
                (#{reportId}, #{username}, #{message}, #{risk}, #{emotion}, #{confidence}, #{status},
                 #{attempts}, #{lastError}, #{createdAt}, #{updatedAt})
            """)
    @Options(useGeneratedKeys = true, keyProperty = "id")
    void insert(RiskAlertEvent row);

    @Update("""
            UPDATE risk_alert_events SET
                status = #{status}, attempts = #{attempts}, last_error = #{lastError}, updated_at =
                #{updatedAt}
            WHERE id = #{id}
            """)
    int update(RiskAlertEvent row);
}
