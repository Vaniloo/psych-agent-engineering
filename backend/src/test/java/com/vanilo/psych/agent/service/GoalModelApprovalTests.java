package com.vanilo.psych.agent.service;

import static org.junit.jupiter.api.Assertions.*;

import com.fasterxml.jackson.databind.ObjectMapper;
import java.nio.file.Path;
import org.junit.jupiter.api.Test;

class GoalModelApprovalTests {
    @Test void pythonProofBindsOwnerTurnSourceTargetVersionOutcomeAndExpiry() throws Exception {
        var f=new ObjectMapper().readTree(Path.of("../agent-runtime/tests/fixtures/goal-model-approval.json").toFile());
        String token=f.path("token").asText(), secret=f.path("secret").asText(), sha=f.path("sha").asText();
        String text=f.path("message").asText();long now=f.path("now").asLong();
        assertTrue(GoalModelApproval.verifyAt(token,secret,sha,"alice",2L,"test-receipt",text,"progress",7L,"first",1L,"done",now));
        assertFalse(GoalModelApproval.verifyAt(token,secret,sha,"bob",2L,"test-receipt",text,"progress",7L,"first",1L,"done",now));
        assertFalse(GoalModelApproval.verifyAt(token,secret,sha,"alice",3L,"test-receipt",text,"progress",7L,"first",1L,"done",now));
        assertFalse(GoalModelApproval.verifyAt(token,secret,sha,"alice",2L,"other-receipt",text,"progress",7L,"first",1L,"done",now));
        assertFalse(GoalModelApproval.verifyAt(token,secret,sha,"alice",2L,"test-receipt",text+"？","progress",7L,"first",1L,"done",now));
        assertFalse(GoalModelApproval.verifyAt(token,secret,sha,"alice",2L,"test-receipt",text,"progress",8L,"first",1L,"done",now));
        assertFalse(GoalModelApproval.verifyAt(token,secret,sha,"alice",2L,"test-receipt",text,"progress",7L,"second",1L,"done",now));
        assertFalse(GoalModelApproval.verifyAt(token,secret,sha,"alice",2L,"test-receipt",text,"progress",7L,"first",2L,"done",now));
        assertFalse(GoalModelApproval.verifyAt(token,secret,sha,"alice",2L,"test-receipt",text,"progress",7L,"first",1L,"partial",now));
        assertFalse(GoalModelApproval.verifyAt(token,secret,"b".repeat(64),"alice",2L,"test-receipt",text,"progress",7L,"first",1L,"done",now));
        assertFalse(GoalModelApproval.verifyAt(token,secret,sha,"alice",2L,"test-receipt",text,"create",null,null,null,null,now));
        assertFalse(GoalModelApproval.verifyAt(token,secret,sha,"alice",2L,"test-receipt",text,"progress",7L,"first",1L,"done",now+301));
        assertFalse(GoalModelApproval.verifyAt(token+"0",secret,sha,"alice",2L,"test-receipt",text,"progress",7L,"first",1L,"done",now));
    }
}
