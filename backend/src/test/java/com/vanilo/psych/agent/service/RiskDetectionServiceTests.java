package com.vanilo.psych.agent.service;

import static org.junit.jupiter.api.Assertions.*;

import org.junit.jupiter.api.Test;

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

    @Test
    void currentSafetyDenialDoesNotBecomeUrgentJustBecauseItSaysNow() {
        assertFalse(service.isHighRisk("我现在安全，不再有伤害自己的想法。"));
        assertFalse(service.isHighRisk("我现在不会自杀，只想聊聊压力。"));
    }

    @Test
    void denialOfOneActionDoesNotSuppressAnotherActualDanger() {
        assertTrue(service.isHighRisk("我没有自杀想法，但我想割腕。"));
        assertTrue(service.isHighRisk("我不想死，但已经准备好了遗书。"));
    }
}
