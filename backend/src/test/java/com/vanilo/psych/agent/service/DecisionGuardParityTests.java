package com.vanilo.psych.agent.service;

import static org.junit.jupiter.api.Assertions.*;
import com.fasterxml.jackson.databind.ObjectMapper;
import java.nio.file.Path;
import org.junit.jupiter.api.Test;

class DecisionGuardParityTests {
    @Test
    void riskScannerMatchesSharedCases() throws Exception {
        var file=Path.of(System.getProperty("basedir"),"..","contracts","decision-guard-cases.json").normalize().toFile();
        var root=new ObjectMapper().readTree(file);
        var scanner=new RiskDetectionService();
        for(var row:root.get("risk")) assertEquals(row.get("highRisk").asBoolean(),scanner.isHighRisk(row.get("text").asText()),row.get("text").asText());
    }
}
