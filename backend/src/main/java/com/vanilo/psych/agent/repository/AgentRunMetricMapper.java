package com.vanilo.psych.agent.repository;

import com.vanilo.psych.agent.entity.AgentRunMetric;
import java.time.LocalDateTime;
import java.util.List;
import org.apache.ibatis.annotations.*;

@Mapper
public interface AgentRunMetricMapper {
    @Select("SELECT EXISTS(SELECT 1 FROM agent_run_metrics WHERE request_id = #{requestId})")
    boolean existsByRequestId(String requestId);

    @Select("""
            SELECT * FROM agent_run_metrics
            WHERE username = #{username} AND started_at > #{since} ORDER BY started_at, id
            """)
    List<AgentRunMetric> since(String username, LocalDateTime since);

    @Select("""
            SELECT * FROM agent_run_metrics
            WHERE username = #{username} AND started_at > #{since}
            ORDER BY started_at DESC, id DESC LIMIT #{limit}
            """)
    List<AgentRunMetric> recent(String username, LocalDateTime since, int limit);

    @Insert("""
            INSERT INTO agent_run_metrics
                (request_id, username, session_id, model, status, total_duration_ms, ttft_ms, prompt_tokens,
                 completion_tokens, total_tokens, tokens_per_second, actual_cost_usd, estimated_cost_usd,
                 cost_mode, mcp_duration_ms, fallback_count, persistence_status, node_durations_json,
                 retrieval_json, model_calls_json, pipeline_json, capability_calls_json, started_at,
                 created_at)
            VALUES
                (#{requestId}, #{username}, #{sessionId}, #{model}, #{status}, #{totalDurationMs},
                 #{ttftMs}, #{promptTokens}, #{completionTokens}, #{totalTokens}, #{tokensPerSecond},
                 #{actualCostUsd}, #{estimatedCostUsd}, #{costMode}, #{mcpDurationMs}, #{fallbackCount},
                 #{persistenceStatus}, #{nodeDurationsJson}, #{retrievalJson}, #{modelCallsJson},
                 #{pipelineJson}, #{capabilityCallsJson}, #{startedAt}, #{createdAt})
            """)
    @Options(useGeneratedKeys = true, keyProperty = "id")
    void insert(AgentRunMetric row);
}
