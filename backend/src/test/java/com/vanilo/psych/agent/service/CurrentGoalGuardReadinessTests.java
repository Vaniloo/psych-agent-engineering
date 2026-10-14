package com.vanilo.psych.agent.service;

import static org.junit.jupiter.api.Assertions.*;
import static org.mockito.Mockito.*;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.vanilo.psych.agent.dto.*;
import com.vanilo.psych.agent.entity.*;
import com.vanilo.psych.agent.repository.*;
import java.util.*;
import org.junit.jupiter.api.Test;

/** Regression checks use synthetic receipts and mocked repositories: no business writes or generation. */
class CurrentGoalGuardReadinessTests {
    static class WriteBoundaryReached extends RuntimeException {}

    @Test
    void sixWrongModelSelectionsAreStoppedBeforeTheWriteBoundary() {
        String[][] probes = {
            {"她今天已经把手机放客厅充了十分钟，这是她做的。", "done"},
            {"昨天系统已经记录过我给表姐发了问候，我只是回顾旧记录，请别再次同步。", "done"},
            {"我已经列好了两样需要买的东西，不是两件烦心事。", "partial"},
            {"我今天已经把平板拿到卧室充电十分钟了，手机没动。", "done"},
            {"我已经写了日记但没写两件烦心事便签，别把主题相似当作完成。", "partial"},
            {"今天写两件烦心事这一步卡住了，我一拿笔就脑子空白，需要一点帮助。", "done"},
        };
        for (String[] probe : probes) {
            var users = mock(UserRepository.class);
            var goals = mock(SupportGoalRepository.class);
            var receipts = mock(AgentTurnReceiptRepository.class);
            var checkIns = mock(GoalCheckInRepository.class);
            User owner = new User();
            owner.setUsername("readiness"); owner.setGoalAutoSync(true);
            SupportGoal goal = new SupportGoal();
            goal.setId(901L); goal.setUser(owner); goal.setTitle("合成目标");
            goal.setActionsJson("[{\"id\":\"911\",\"title\":\"把手机放到客厅充电十分钟，写两件烦心事，给表姐发问候\",\"status\":\"planned\",\"feedback\":[]}]");
            AgentTurnReceipt receipt = new AgentTurnReceipt();
            receipt.setUser(owner); receipt.setSessionId(777L); receipt.setUserMessage(probe[0]);
            when(users.findByUsername("readiness")).thenReturn(Optional.of(owner));
            when(goals.findByIdAndUser(901L, owner)).thenReturn(Optional.of(goal));
            when(receipts.findByUserAndRequestId(owner, "readiness-request")).thenReturn(Optional.of(receipt));
            var service = spy(new GoalService(goals, users, new ObjectMapper(), checkIns, receipts));
            doThrow(new WriteBoundaryReached()).when(service).checkIn(eq("readiness"), eq(901L), any());
            var request = new GoalSyncRequest("readiness", "readiness-request", 777L, 901L, "911", 1L, probe[1], probe[0]);
            var result=service.syncFromTurn(request);
            assertEquals(false,result.get("applied"),probe[0]);
            verify(service,never()).checkIn(anyString(),anyLong(),any());
            verify(goals, never()).saveAndFlush(any());
            verify(checkIns, never()).saveAndFlush(any());
        }
    }

    @Test
    void pastCreationQuoteIsRejectedBeforeGeneration() {
        var users = mock(UserRepository.class);
        var receipts = mock(AgentTurnReceiptRepository.class);
        var records = mock(SupportGoalRepository.class);
        var goals = mock(GoalService.class);
        var llm = mock(LlmService.class);
        User owner = new User(); owner.setUsername("readiness");
        AgentTurnReceipt receipt = new AgentTurnReceipt();
        receipt.setSessionId(777L);
        receipt.setUserMessage("我昨天已经让你创建过这个目标，今天只是回顾。");
        when(users.findByUsername("readiness")).thenReturn(Optional.of(owner));
        when(receipts.findOwnedForGoalCreation(owner, "readiness-request")).thenReturn(Optional.of(receipt));
        var service = new GoalCreationService(users, receipts, records, goals, llm, new ObjectMapper());
        var result = service.createFromTurn(new GoalCreateRequest("readiness", "readiness-request", 777L));
        assertEquals("not_explicit", result.get("reason"));
        verifyNoInteractions(llm);
        verifyNoInteractions(goals);
        verify(receipts, never()).saveAndFlush(any());
    }

    @Test
    void entityBindingsStopWrongWritesButPreserveActualCompletionAndPartialProgress() throws Exception {
        ObjectMapper json = new ObjectMapper();
        String[][] cases = {
            {"我刚给老板发了一条近况消息。", "给阿姨发一条近况消息", "done", "false"},
            {"我已经把手机放到卧室充电十分钟了。", "把手机放到客厅充电十分钟", "done", "false"},
            {"我已经散步了两分钟，又休息了十分钟。", "散步十分钟", "done", "false"},
            {"我刚给阿姨发了一条近况消息。", "给阿姨发一条近况消息", "done", "true"},
            {"我已经把手机放到客厅充电十二分钟了。", "把手机放到客厅充电十分钟", "done", "true"},
            {"我已经把手机放到客厅充电两分钟了。", "把手机放到客厅充电十分钟", "partial", "true"},
        };
        for (String[] c : cases) {
            var users = mock(UserRepository.class);
            var goals = mock(SupportGoalRepository.class);
            var receipts = mock(AgentTurnReceiptRepository.class);
            var checkIns = mock(GoalCheckInRepository.class);
            User owner = new User(); owner.setUsername("binding-fixture"); owner.setGoalAutoSync(true);
            SupportGoal goal = new SupportGoal(); goal.setId(1L); goal.setUser(owner); goal.setTitle("合成目标");
            goal.setActionsJson(json.writeValueAsString(List.of(new GoalRequest.Action("step", c[1], "planned", List.of()))));
            AgentTurnReceipt receipt = new AgentTurnReceipt(); receipt.setSessionId(7L); receipt.setUserMessage(c[0]);
            when(users.findByUsername(owner.getUsername())).thenReturn(Optional.of(owner));
            when(goals.findByIdAndUser(1L,owner)).thenReturn(Optional.of(goal));
            when(receipts.findByUserAndRequestId(owner,"binding-request")).thenReturn(Optional.of(receipt));
            var service = spy(new GoalService(goals,users,json,checkIns,receipts));
            doThrow(new WriteBoundaryReached()).when(service).checkIn(eq(owner.getUsername()),eq(1L),any());
            var request = new GoalSyncRequest(owner.getUsername(),"binding-request",7L,1L,"step",1L,c[2],c[0]);
            if (Boolean.parseBoolean(c[3])) {
                assertThrows(WriteBoundaryReached.class, () -> service.syncFromTurn(request), c[0]);
            } else {
                assertEquals(false, service.syncFromTurn(request).get("applied"), c[0]);
                verify(service,never()).checkIn(anyString(),anyLong(),any());
            }
            verify(goals,never()).saveAndFlush(any());
            verify(checkIns,never()).saveAndFlush(any());
        }
    }
}
