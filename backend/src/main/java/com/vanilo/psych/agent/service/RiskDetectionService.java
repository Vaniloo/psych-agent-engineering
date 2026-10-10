package com.vanilo.psych.agent.service;

import org.springframework.stereotype.Service;

import java.util.List;

@Service
public class RiskDetectionService {
    private static final List<String> HIGH_RISK_PHRASES =
            List.of(
                    "想死", "不想活", "自杀", "轻生", "结束自己", "活着没意义", "伤害自己", "割腕", "跳楼", "服药自杀", "准备去死",
                    "结束生命");
    private static final List<String> NEGATED_PHRASES =
            List.of(
                    "不再有伤害自己的想法",
                    "没有伤害自己的想法",
                    "不会伤害自己",
                    "不想伤害自己",
                    "不想自杀",
                    "没有自杀想法",
                    "不会自杀",
                    "并不想死",
                    "不是想死",
                    "不想死");
    private static final List<String> IMMEDIACY_PHRASES = List.of("马上", "已经准备", "具体计划", "遗书");

    public boolean isHighRisk(String message) {
        if (message == null || message.isBlank()) {
            return false;
        }
        String normalized = message.toLowerCase().replaceAll("[\\s，。！？,.!?、;；:：\"'“”‘’]+", "");
        String remaining = normalized;
        for (String phrase : NEGATED_PHRASES) remaining = remaining.replace(phrase, "");
        String unnegated = remaining;
        if (HIGH_RISK_PHRASES.stream().anyMatch(unnegated::contains)) return true;
        // "现在" alone must not overturn a denial. Concrete preparation remains
        // cautious even when another part of the sentence denies wanting harm.
        return HIGH_RISK_PHRASES.stream().anyMatch(normalized::contains)
                && IMMEDIACY_PHRASES.stream().anyMatch(normalized::contains);
    }
}
