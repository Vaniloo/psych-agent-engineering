package com.vanilo.psych.agent.service;

import org.junit.jupiter.api.Test;

import static org.junit.jupiter.api.Assertions.*;

class RiskDetectionServiceTests {
    private final RiskDetectionService service = new RiskDetectionService();

    @Test
    void detectsExplicitSelfHarmIntent() {
        assertTrue(service.isHighRisk("我不想活了，准备去死"));
    }

    @Test
    void doesNotEscalateOrdinaryAnxiety() {
        assertFalse(service.isHighRisk("最近考试压力很大，有点焦虑和失眠"));
    }

    @Test
    void understandsExplicitNegationWithoutImmediatePlan() {
        assertFalse(service.isHighRisk("我没有自杀想法，只是最近压力很大"));
    }
}
