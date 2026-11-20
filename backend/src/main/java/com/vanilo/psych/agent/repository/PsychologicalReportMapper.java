package com.vanilo.psych.agent.repository;

import com.vanilo.psych.agent.dto.EmotionTrendResponse;
import com.vanilo.psych.agent.dto.ReportSummaryResponse;
import com.vanilo.psych.agent.dto.RiskCountResponse;
import com.vanilo.psych.agent.dto.TopRiskUserResponse;
import com.vanilo.psych.agent.entity.PsychologicalReport;
import com.vanilo.psych.agent.entity.User;
import java.time.LocalDateTime;
import java.util.List;
import java.util.Optional;
import org.apache.ibatis.annotations.*;
import org.springframework.data.domain.Pageable;

@Mapper
public interface PsychologicalReportMapper {
    @Select("""
            SELECT t.*, u.username FROM psychological_reports t LEFT JOIN users u ON u.id = t.user_id
            WHERE t.id = #{id}
            """)
    @Results(id = "row", value = {
        @Result(column = "user_id", property = "user.id"),
        @Result(column = "username", property = "user.username")
    })
    Optional<PsychologicalReport> findById(Long id);

    @Select("SELECT t.*, u.username FROM psychological_reports t LEFT JOIN users u ON u.id = t.user_id")
    @ResultMap("row")
    List<PsychologicalReport> findAll();

    @Select("""
            SELECT t.*, u.username FROM psychological_reports t LEFT JOIN users u ON u.id = t.user_id
            ORDER BY t.created_at DESC, t.id DESC LIMIT #{pageSize} OFFSET #{offset}
            """)
    @ResultMap("row")
    List<PsychologicalReport> page(Pageable page);

    @Select("SELECT COUNT(*) FROM psychological_reports")
    long count();

    @Select("""
            SELECT t.*, u.username FROM psychological_reports t LEFT JOIN users u ON u.id = t.user_id
            WHERE t.user_id = #{user.id}
            """)
    @ResultMap("row")
    List<PsychologicalReport> findByUser(@Param("user") User user);

    @Select("""
            SELECT id, message, risk, emotion, confidence, created_at FROM psychological_reports
            WHERE user_id = #{userId} ORDER BY created_at DESC, id DESC LIMIT #{limit}
            """)
    List<ReportSummaryResponse> recent(Long userId, int limit);

    @Select("""
            SELECT user_id, COUNT(*) AS count FROM psychological_reports WHERE risk = 'high'
            GROUP BY user_id ORDER BY count DESC, user_id LIMIT #{limit}
            """)
    List<TopRiskUserResponse> topRiskUsers(int limit);

    @Select("""
            SELECT risk, COUNT(*) AS count FROM psychological_reports
            WHERE user_id = #{userId} GROUP BY risk
            """)
    List<RiskCountResponse> findRiskDistributionByUserId(Long userId);

    @Select("""
            SELECT DATE(created_at) AS date, emotion, COUNT(*) AS count, AVG(confidence) AS average_confidence
            FROM psychological_reports WHERE user_id = #{userId} AND created_at >= #{since}
            GROUP BY DATE(created_at), emotion ORDER BY DATE(created_at), emotion
            """)
    List<EmotionTrendResponse> findEmotionTrendByUserId(Long userId, LocalDateTime since);

    @Select("SELECT risk, COUNT(*) AS count FROM psychological_reports GROUP BY risk")
    List<RiskCountResponse> findRiskDistribution();

    @Select("""
            SELECT DATE(created_at) AS date, emotion, COUNT(*) AS count, AVG(confidence) AS average_confidence
            FROM psychological_reports WHERE created_at >= #{since}
            GROUP BY DATE(created_at), emotion ORDER BY DATE(created_at), emotion
            """)
    List<EmotionTrendResponse> findEmotionTrend(LocalDateTime since);

    @Insert("""
            INSERT INTO psychological_reports
                (message, risk, emotion, confidence, created_at, user_id)
            VALUES
                (#{message}, #{risk}, #{emotion}, #{confidence}, #{createdAt}, #{user.id})
            """)
    @Options(useGeneratedKeys = true, keyProperty = "id")
    void insert(PsychologicalReport row);
}
