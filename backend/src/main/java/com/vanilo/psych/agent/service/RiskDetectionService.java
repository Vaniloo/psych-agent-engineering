package com.vanilo.psych.agent.service;

import org.springframework.stereotype.Service;

import java.util.List;

@Service
public class RiskDetectionService {
    private static final List<String> HIGH_RISK_PHRASES = List.of(
            "想死", "不想活", "自杀", "轻生", "结束自己", "活着没意义",
            "伤害自己", "割腕", "跳楼", "服药自杀", "准备去死", "结束生命"
    );
    private static final List<String> NEGATED_PHRASES = List.of(
            "不想自杀", "没有自杀想法", "不会自杀", "并不想死", "不是想死"
    );
    private static final List<String> IMMEDIACY_PHRASES = List.of(
            "现在", "马上", "今晚", "已经准备", "计划", "方法", "遗书"
    );

    public boolean isHighRisk(String message) {
        if (message == null || message.isBlank()) {
            return false;
        }
        String normalized = message.toLowerCase()
                .replaceAll("[\\s，。！？,.!?、;；:：\"'“”‘’]+", "");
        long matches = HIGH_RISK_PHRASES.stream().filter(normalized::contains).count();
        if (matches == 0) {
            return false;
        }
        boolean negated = NEGATED_PHRASES.stream().anyMatch(normalized::contains);
        boolean immediate = IMMEDIACY_PHRASES.stream().anyMatch(normalized::contains);
        return !negated || immediate;
    }
}
