package com.vanilo.psych.agent.service;

import com.fasterxml.jackson.core.JsonProcessingException;
import com.fasterxml.jackson.core.type.TypeReference;
import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.vanilo.psych.agent.dto.AgentRunMetricsResponse;
import com.vanilo.psych.agent.entity.AgentRunMetric;
import com.vanilo.psych.agent.entity.RagEvaluationMetric;
import com.vanilo.psych.agent.repository.AgentRunMetricMapper;
import com.vanilo.psych.agent.repository.RagEvaluationMetricMapper;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.time.LocalDate;
import java.time.LocalDateTime;
import java.time.OffsetDateTime;
import java.time.ZoneId;
import java.util.*;

@Service
public class AgentMetricsService {
    private final AgentRunMetricMapper runMapper;
    private final RagEvaluationMetricMapper evaluationMapper;
    private final ObjectMapper objectMapper;

    public AgentMetricsService(AgentRunMetricMapper runMapper,
                               RagEvaluationMetricMapper evaluationMapper,
                               ObjectMapper objectMapper) {
        this.runMapper = runMapper;
        this.evaluationMapper = evaluationMapper;
        this.objectMapper = objectMapper;
    }

    @Transactional
    public void recordRun(String username,
                          Long sessionId,
                          AgentRunMetricsResponse metrics) {
        if (metrics == null
                || metrics.getRequestId() == null
                || runMapper.existsByRequestId(metrics.getRequestId())) {
            return;
        }
        LocalDateTime startedAt = parseDateTime(metrics.getStartedAt());
        AgentRunMetric entity = new AgentRunMetric(
                null,
                metrics.getRequestId(),
                username,
                sessionId,
                defaultString(metrics.getModel(), "unknown"),
                defaultString(metrics.getStatus(), "unknown"),
                defaultDouble(metrics.getTotalDurationMs()),
                metrics.getTtftMs(),
                defaultLong(metrics.getPromptTokens()),
                defaultLong(metrics.getCompletionTokens()),
                defaultLong(metrics.getTotalTokens()),
                defaultDouble(metrics.getTokensPerSecond()),
                defaultDouble(metrics.getActualCostUsd()),
                defaultDouble(metrics.getEstimatedCostUsd()),
                defaultString(metrics.getCostMode(), "local"),
                defaultDouble(metrics.getMcpDurationMs()),
                metrics.getFallbackCount() == null ? 0 : metrics.getFallbackCount(),
                defaultString(metrics.getPersistenceStatus(), "unknown"),
                writeJson(metrics.getNodeDurationsMs()),
                writeJson(metrics.getRetrieval()),
                writeJson(metrics.getModelCalls()),
                writeJson(metrics.getPipeline()),
                writeJson(metrics.getCapabilityCalls()),
                startedAt,
                LocalDateTime.now()
        );
        runMapper.insert(entity);
    }

    @Transactional(readOnly = true)
    public Map<String, Object> summary(String username, int requestedDays) {
        int days = Math.min(90, Math.max(1, requestedDays));
        List<AgentRunMetric> rows =
                runMapper.since(
                        username,
                        LocalDateTime.now().minusDays(days)
                );
        List<Double> durations = rows.stream()
                .map(AgentRunMetric::getTotalDurationMs)
                .sorted()
                .toList();
        List<Double> ttfts = rows.stream()
                .map(AgentRunMetric::getTtftMs)
                .filter(Objects::nonNull)
                .toList();
        List<Double> ragDurations = new ArrayList<>();
        int cacheHits = 0;
        int ragRuns = 0;
        for (AgentRunMetric row : rows) {
            Map<String, Object> retrieval = readMap(row.getRetrievalJson());
            if (!retrieval.isEmpty()) {
                ragRuns++;
                ragDurations.add(asDouble(retrieval.get("durationMs")));
                cacheHits += Boolean.TRUE.equals(retrieval.get("cacheHit")) ? 1 : 0;
            }
        }

        Map<String, Object> response = new LinkedHashMap<>();
        response.put("windowDays", days);
        response.put("totalRuns", rows.size());
        response.put("avgDurationMs", average(durations));
        response.put("p50DurationMs", percentile(durations, 0.50));
        response.put("p95DurationMs", percentile(durations, 0.95));
        response.put("p99DurationMs", percentile(durations, 0.99));
        response.put("avgTtftMs", ttfts.isEmpty()?null:average(ttfts));
        response.put("totalPromptTokens", rows.stream().mapToLong(AgentRunMetric::getPromptTokens).sum());
        response.put("totalCompletionTokens", rows.stream().mapToLong(AgentRunMetric::getCompletionTokens).sum());
        response.put("totalTokens", rows.stream().mapToLong(AgentRunMetric::getTotalTokens).sum());
        response.put("actualCostUsd", round(rows.stream().mapToDouble(AgentRunMetric::getActualCostUsd).sum(), 8));
        response.put("estimatedCostUsd", round(rows.stream().mapToDouble(AgentRunMetric::getEstimatedCostUsd).sum(), 8));
        response.put("successRate", ratio(
                rows.stream().filter(row -> "success".equals(row.getStatus())).count(),
                rows.size()
        ));
        response.put("fallbackRate", ratio(
                rows.stream().filter(row -> row.getFallbackCount() > 0).count(),
                rows.size()
        ));
        response.put("avgRagDurationMs", average(ragDurations));
        response.put("ragCacheHitRate", ratio(cacheHits, ragRuns));
        response.put("trend", buildTrend(rows));
        response.put("ragRuns",ragRuns);response.put("ttftSamples",ttfts.size());
        response.put("pipelineSamples",rows.stream().filter(r->!readMap(r.getPipelineJson()).isEmpty()).count());
        Map<String,List<Double>> nodes=new LinkedHashMap<>();Map<String,Long> routes=new LinkedHashMap<>();Map<String,List<Map<String,Object>>> calls=new LinkedHashMap<>();
        for(var row:rows){readMap(row.getNodeDurationsJson()).forEach((name,value)->nodes.computeIfAbsent(name,n->new ArrayList<>()).add(asDouble(value)));
            var pipeline=readMap(row.getPipelineJson());if(pipeline.get("router") instanceof Map<?,?> router){String route=Objects.toString(router.get("supportLevel"),"unknown");routes.merge(route,1L,Long::sum);}
            for(var call:readList(row.getCapabilityCallsJson()))calls.computeIfAbsent(Objects.toString(call.get("name"),"unknown"),n->new ArrayList<>()).add(call);
        }
        response.put("nodeLatency",nodes.entrySet().stream().map(e->Map.of("name",e.getKey(),"avgDurationMs",average(e.getValue()),"samples",e.getValue().size())).toList());
        response.put("routes",routes.entrySet().stream().map(e->Map.of("name",e.getKey(),"count",e.getValue())).toList());
        response.put("capabilities",calls.entrySet().stream().map(e->Map.of("name",e.getKey(),"calls",e.getValue().size(),"errors",e.getValue().stream().filter(c->"error".equals(c.get("status"))).count(),"avgDurationMs",average(e.getValue().stream().map(c->asDouble(c.get("durationMs"))).toList()))).toList());
        return response;
    }

    @Transactional(readOnly = true)
    public List<Map<String, Object>> recent(String username, int requestedLimit,int requestedDays) {
        int limit = Math.min(100, Math.max(1, requestedLimit));
        return runMapper.recent(
                        username,LocalDateTime.now().minusDays(Math.min(90,Math.max(1,requestedDays))),
                        limit
                )
                .stream()
                .map(this::toRecentMap)
                .toList();
    }

    @Transactional
    public JsonNode recordEvaluation(JsonNode evaluation) {
        RagEvaluationMetric entity = new RagEvaluationMetric(
                null,
                evaluation.path("datasetVersion").asText("unknown"),
                evaluation.path("sampleCount").asInt(),
                evaluation.path("recallAt1").asDouble(),
                evaluation.path("recallAt3").asDouble(),
                evaluation.path("recallAt5").asDouble(),
                evaluation.path("precisionAt5").asDouble(),
                evaluation.path("hitRateAt5").asDouble(),
                evaluation.path("mrr").asDouble(),
                evaluation.path("nDcgAt5").asDouble(),
                evaluation.path("avgLatencyMs").asDouble(),
                evaluation.path("p95LatencyMs").asDouble(),
                evaluation.path("cacheHitRate").asDouble(),
                evaluation.path("cases").toString(),
                parseDateTime(evaluation.path("evaluatedAt").asText()),
                LocalDateTime.now()
        );
        evaluationMapper.insert(entity);
        return evaluation;
    }

    @Transactional(readOnly = true)
    public Map<String, Object> latestEvaluation() {
        return evaluationMapper.latest()
                .map(this::toEvaluationMap)
                .orElseGet(LinkedHashMap::new);
    }

    private Map<String, Object> toRecentMap(AgentRunMetric row) {
        Map<String, Object> result = new LinkedHashMap<>();
        result.put("requestId", row.getRequestId());
        result.put("sessionId", row.getSessionId());
        result.put("model", row.getModel());
        result.put("status", row.getStatus());
        result.put("totalDurationMs", row.getTotalDurationMs());
        result.put("ttftMs", row.getTtftMs());
        result.put("promptTokens", row.getPromptTokens());
        result.put("completionTokens", row.getCompletionTokens());
        result.put("totalTokens", row.getTotalTokens());
        result.put("tokensPerSecond", row.getTokensPerSecond());
        result.put("actualCostUsd", row.getActualCostUsd());
        result.put("estimatedCostUsd", row.getEstimatedCostUsd());
        result.put("costMode", row.getCostMode());
        result.put("mcpDurationMs", row.getMcpDurationMs());
        result.put("fallbackCount", row.getFallbackCount());
        result.put("persistenceStatus", row.getPersistenceStatus());
        result.put("nodeDurationsMs", readMap(row.getNodeDurationsJson()));
        result.put("retrieval", readMap(row.getRetrievalJson()));
        result.put("startedAt", row.getStartedAt());
        result.put("pipeline",readMap(row.getPipelineJson()));result.put("capabilityCalls",readList(row.getCapabilityCallsJson()));
        return result;
    }

    private List<Map<String, Object>> buildTrend(List<AgentRunMetric> rows) {
        Map<LocalDate, List<AgentRunMetric>> grouped = new TreeMap<>();
        rows.forEach(row -> grouped.computeIfAbsent(
                row.getStartedAt().toLocalDate(),
                ignored -> new ArrayList<>()
        ).add(row));
        return grouped.entrySet().stream().map(entry -> {
            List<AgentRunMetric> dayRows = entry.getValue();
            Map<String, Object> point = new LinkedHashMap<>();
            point.put("date", entry.getKey());
            point.put("runCount", dayRows.size());
            point.put("avgDurationMs", average(dayRows.stream()
                    .map(AgentRunMetric::getTotalDurationMs).toList()));
            var firstTokens=dayRows.stream().map(AgentRunMetric::getTtftMs).filter(Objects::nonNull).toList();
            point.put("avgTtftMs", firstTokens.isEmpty()?null:average(firstTokens));point.put("ttftSamples",firstTokens.size());
            point.put("totalTokens", dayRows.stream()
                    .mapToLong(AgentRunMetric::getTotalTokens).sum());
            point.put("estimatedCostUsd", round(dayRows.stream()
                    .mapToDouble(AgentRunMetric::getEstimatedCostUsd).sum(), 8));
            return point;
        }).toList();
    }

    private Map<String, Object> toEvaluationMap(RagEvaluationMetric row) {
        Map<String, Object> result = new LinkedHashMap<>();
        result.put("datasetVersion", row.getDatasetVersion());
        result.put("sampleCount", row.getSampleCount());
        result.put("recallAt1", row.getRecallAt1());
        result.put("recallAt3", row.getRecallAt3());
        result.put("recallAt5", row.getRecallAt5());
        result.put("precisionAt5", row.getPrecisionAt5());
        result.put("hitRateAt5", row.getHitRateAt5());
        result.put("mrr", row.getMrr());
        result.put("nDcgAt5", row.getNDcgAt5());
        result.put("avgLatencyMs", row.getAvgLatencyMs());
        result.put("p95LatencyMs", row.getP95LatencyMs());
        result.put("cacheHitRate", row.getCacheHitRate());
        result.put("cases", readList(row.getCasesJson()));
        result.put("evaluatedAt", row.getEvaluatedAt());
        return result;
    }

    private String writeJson(Object value) {
        try {
            return objectMapper.writeValueAsString(value == null ? Map.of() : value);
        } catch (JsonProcessingException exception) {
            return "{}";
        }
    }

    private Map<String, Object> readMap(String value) {
        try {
            return value == null || value.isBlank()
                    ? Map.of()
                    : objectMapper.readValue(value, new TypeReference<>() {});
        } catch (JsonProcessingException exception) {
            return Map.of();
        }
    }

    private List<Map<String, Object>> readList(String value) {
        try {
            return value == null || value.isBlank()
                    ? List.of()
                    : objectMapper.readValue(value, new TypeReference<>() {});
        } catch (JsonProcessingException exception) {
            return List.of();
        }
    }

    private static LocalDateTime parseDateTime(String value) {
        try {
            return OffsetDateTime.parse(value)
                    .atZoneSameInstant(ZoneId.systemDefault())
                    .toLocalDateTime();
        } catch (RuntimeException exception) {
            return LocalDateTime.now();
        }
    }

    private static double average(List<Double> values) {
        return values.isEmpty()
                ? 0.0
                : round(values.stream().mapToDouble(Double::doubleValue).average().orElse(0.0), 2);
    }

    private static double percentile(List<Double> sortedValues, double quantile) {
        if (sortedValues.isEmpty()) {
            return 0.0;
        }
        int index = Math.max(0, (int) Math.ceil(sortedValues.size() * quantile) - 1);
        return round(sortedValues.get(index), 2);
    }

    private static double ratio(long numerator, long denominator) {
        return denominator == 0 ? 0.0 : round((double) numerator / denominator, 4);
    }

    private static double round(double value, int places) {
        double factor = Math.pow(10, places);
        return Math.round(value * factor) / factor;
    }

    private static double asDouble(Object value) {
        if (value instanceof Number number) {
            return number.doubleValue();
        }
        try {
            return Double.parseDouble(String.valueOf(value));
        } catch (RuntimeException exception) {
            return 0.0;
        }
    }

    private static String defaultString(String value, String fallback) {
        return value == null || value.isBlank() ? fallback : value;
    }

    private static double defaultDouble(Double value) {
        return value == null ? 0.0 : value;
    }

    private static long defaultLong(Long value) {
        return value == null ? 0L : value;
    }
}
