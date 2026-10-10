package com.vanilo.psych.agent.dto;

import java.time.LocalDate;
import java.time.LocalDateTime;
import java.util.List;

/** An owned, evidence-linked reflection, never a diagnostic score. */
public record ConversationOverviewResponse(int days, LocalDate periodStart, LocalDate periodEnd,
        LocalDateTime generatedAt, String status, String summary, List<Long> summaryMessageIds,
        Coverage coverage, List<Insight> themes, List<EmotionObservation> emotionObservations,
        List<String> reflectionQuestions, List<Source> sources, List<Evidence> evidence, List<DayActivity> activity) {
    public record Coverage(int conversationCount, int expressionCount, int activeDays, boolean sampled) {}
    public record Insight(String title, String description, List<Long> messageIds) {}
    public record EmotionObservation(Long messageId, String category, String quote) {}
    public record Source(Long sessionId, String title, LocalDateTime lastMentionAt, int expressionCount) {}
    public record Evidence(Long messageId, Long sessionId, String text, LocalDateTime createdAt) {}
    public record DayActivity(LocalDate date, int expressionCount) {}
}
