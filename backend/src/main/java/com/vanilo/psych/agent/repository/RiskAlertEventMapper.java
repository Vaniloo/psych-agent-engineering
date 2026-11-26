package com.vanilo.psych.agent.repository;

import com.vanilo.psych.agent.entity.RiskAlertEvent;
import java.util.List;
import org.apache.ibatis.annotations.*;

@Mapper
public interface RiskAlertEventMapper {
    @Insert("""
            INSERT INTO risk_alert_events (username, message, risk, status, created_at)
            VALUES (#{username}, #{message}, #{risk}, #{status}, #{createdAt})
            """)
    @Options(useGeneratedKeys = true, keyProperty = "id")
    void insert(RiskAlertEvent row);

    @Select("SELECT * FROM risk_alert_events WHERE username = #{username} ORDER BY created_at DESC, id DESC")
    List<RiskAlertEvent> findByUsername(String username);
}
