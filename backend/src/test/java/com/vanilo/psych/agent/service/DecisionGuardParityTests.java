package com.vanilo.psych.agent.service;

import static org.junit.jupiter.api.Assertions.*;
import com.fasterxml.jackson.databind.ObjectMapper;
import java.nio.file.Path;
import org.junit.jupiter.api.Test;

/** Python and the actual Java scanner/goal veto consume the same regression fixtures. */
class DecisionGuardParityTests {
    @Test
    void identicalSourceActionAndOutcomePolicy() throws Exception {
        var file=Path.of(System.getProperty("basedir"),"..","contracts","decision-guard-cases.json").normalize().toFile();
        var root=new ObjectMapper().readTree(file);
        var scanner=new RiskDetectionService();
        for(var row:root.get("risk")) assertEquals(row.get("highRisk").asBoolean(),scanner.isHighRisk(row.get("text").asText()),row.get("text").asText());
        for(var row:root.get("progress")) assertEquals(row.get("allowed").asBoolean(),GoalEvidencePolicy.supports(row.get("text").asText(),row.get("title").asText(),row.get("outcome").asText()),row.get("text").asText());
        for(var row:root.get("creation")) assertEquals(row.get("allowed").asBoolean(),GoalCreationService.explicitCreation(row.get("text").asText()),row.get("text").asText());
    }
}
