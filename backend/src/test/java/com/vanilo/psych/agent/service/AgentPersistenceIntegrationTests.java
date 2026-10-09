package com.vanilo.psych.agent.service;

import static org.junit.jupiter.api.Assertions.*;
import static org.mockito.Mockito.*;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.vanilo.psych.agent.dto.*;
import com.vanilo.psych.agent.entity.*;
import com.vanilo.psych.agent.repository.*;

import org.junit.jupiter.api.*;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.autoconfigure.jdbc.AutoConfigureTestDatabase;
import org.springframework.boot.test.autoconfigure.orm.jpa.DataJpaTest;
import org.springframework.context.annotation.Import;
import org.springframework.data.domain.PageRequest;
import org.springframework.data.redis.core.StringRedisTemplate;
import org.springframework.test.context.bean.override.mockito.MockitoBean;

import java.time.LocalDateTime;
import java.util.*;

@DataJpaTest(
        properties = {
            "spring.datasource.url=jdbc:h2:mem:agent;MODE=MySQL;DB_CLOSE_DELAY=-1",
            "spring.datasource.driver-class-name=org.h2.Driver",
            "spring.jpa.hibernate.ddl-auto=create-drop"
        })
@AutoConfigureTestDatabase(replace = AutoConfigureTestDatabase.Replace.NONE)
@Import({ConversationMemoryService.class, GoalService.class, GoalCreationService.class, GoalCoachService.class, GoalWeeklyService.class, AgentMetricsService.class, ObjectMapper.class})
class AgentPersistenceIntegrationTests {
    @MockitoBean LlmService llm;
    @MockitoBean RoleCardService roles;
    @MockitoBean UserProfileService profiles;
    @MockitoBean StringRedisTemplate redis;
    @Autowired UserRepository users;
    @Autowired ConversationSessionRepository sessions;
    @Autowired ConversationMessageRepository messages;
    @Autowired UserProfileRepository profileRecords;
    @Autowired AgentTurnReceiptRepository receipts;
    @Autowired ConversationMemoryService memory;
    @Autowired GoalService goals;
    @Autowired GoalCreationService creation;
    @Autowired GoalCoachService coach;
    @Autowired GoalWeeklyService weekly;
    @Autowired AgentMetricsService metrics;
    User alice;
    ConversationSession session;

    @BeforeEach
    void setup() {
        alice = new User();
        alice.setUsername("alice");
        alice.setPassword("test-only");
        alice.setRole("USER");
        alice = users.saveAndFlush(alice);
        session = new ConversationSession();
        session.setUser(alice);
        session.setTitle("测试");
        session.setCreatedAt(LocalDateTime.now());
        session.setUpdatedAt(LocalDateTime.now());
        session = sessions.saveAndFlush(session);
        UserProfile profile = new UserProfile();
        profile.setUser(alice);
        profile.setUpdatedAt(LocalDateTime.now());
        profile = profileRecords.saveAndFlush(profile);
        when(profiles.getOrCreateProfile(alice)).thenReturn(profile);
    }

    @Test
    void metricsPreserveNewPipelineAndOwnedToolsWhileOldRowsRemainUnknown() {
        var run=new AgentRunMetricsResponse();run.setRequestId("metric-new");run.setStartedAt(java.time.OffsetDateTime.now().toString());run.setModel("local-test");run.setStatus("success");run.setTotalDurationMs(42.0);run.setPersistenceStatus("saved");
        run.setNodeDurationsMs(Map.of("goal_creation",12.0,"goal_progress",3.0));
        run.setPipeline(Map.of("router",Map.of("provider","rules","supportLevel","self_help"),"executor",Map.of("contract","psych-executor-v2"),"memory",Map.of("delivery","queued"),"goals",Map.of("created",1)));
        run.setCapabilityCalls(List.of(Map.of("name","create_goal","durationMs",12.0,"transport","local","status","ok")));
        metrics.recordRun("alice",session.getId(),run);metrics.recordRun("alice",session.getId(),run);
        var summary=metrics.summary("alice",7);assertEquals(1,summary.get("totalRuns"));assertEquals(1L,summary.get("pipelineSamples"));assertEquals(0,summary.get("ttftSamples"));
        assertEquals(1,((List<?>)summary.get("capabilities")).size());
        assertEquals(run.getPipeline(),metrics.recent("alice",30,7).get(0).get("pipeline"));
        assertTrue(metrics.recent("other",30,7).isEmpty());
        var old=new AgentRunMetricsResponse();old.setRequestId("metric-old");old.setStartedAt(java.time.OffsetDateTime.now().toString());metrics.recordRun("alice",session.getId(),old);
        assertEquals(2,metrics.summary("alice",7).get("totalRuns"));assertEquals(1L,metrics.summary("alice",7).get("pipelineSamples"));
    }

    @Test
    void goalCompletionNeedsAllStepsAndPartialOrOtherUsersCannotCompleteIt() {
        var goal=goals.save("alice",null,new GoalRequest("写一句话","","active",null,true,List.of(new GoalRequest.Action("write","写一句话","planned",List.of())),"写出一句话"));
        long id=((Number)goal.get("id")).longValue();var check=goals.checkIn("alice",id,new GoalCheckInRequest("write","done","写好了，感觉比想象中容易",((Number)goal.get("version")).longValue(),true));
        var updated=(Map<?,?>)check.get("goal");var entry=(GoalCheckInResponse)check.get("checkIn");
        var other=new User();other.setUsername("bob");other.setPassword("test-only");other.setRole("USER");users.saveAndFlush(other);
        var request=new GoalReflectRequest(entry.id(),((Number)updated.get("version")).longValue());
        assertThrows(org.springframework.web.server.ResponseStatusException.class,()->coach.reflect("bob",id,request));
        when(llm.complete(anyString(),anyString())).thenReturn("{\"feedback\":\"一句话已经写出，目标达成。\",\"completionCriteria\":\"写出一句话\",\"goalCompleted\":true,\"completionReason\":\"用户报告已完成写一句话这一步\",\"completedEvidenceIds\":[\"write\"],\"nextActionId\":\"\",\"nextStep\":\"\",\"usedMessageIds\":[]}");
        assertEquals("completed",((Map<?,?>)coach.reflect("alice",id,request).get("goal")).get("status"));
        assertEquals(0,weekly.overview("bob",0).get("recordCount"));
    }

    @Test
    void reflectionUsesOwnedFeelingsAndChatPreservesFactsAndIsIdempotent() {
        var saved=goals.save("alice",null,new GoalRequest("开始工作","先从五分钟开始","active",null,true,
                List.of(new GoalRequest.Action("first","打开文档","planned",List.of()),new GoalRequest.Action("second","投入五分钟","planned",List.of())),"写出一个段落"));
        long id=((Number)saved.get("id")).longValue();
        var check=goals.checkIn("alice",id,new GoalCheckInRequest("first","done","打开后感觉没那么难了",((Number)saved.get("version")).longValue(),true));
        var updated=(Map<?,?>)check.get("goal");var entry=(GoalCheckInResponse)check.get("checkIn");
        var opinion=new ConversationMessage();opinion.setUser(alice);opinion.setSession(session);opinion.setRole("user");opinion.setContent("五分钟还是有点多，我想先写一句话。");opinion.setCreatedAt(LocalDateTime.now());messages.saveAndFlush(opinion);
        when(llm.complete(anyString(),anyString())).thenReturn("{\"feedback\":\"打开文档后没那么难，下一步可以再缩小。\",\"completionCriteria\":\"模型不能替换已有标准\",\"goalCompleted\":true,\"completionReason\":\"还有步骤，不能冒充已完成\",\"completedEvidenceIds\":[\"first\"],\"nextActionId\":\"\",\"nextStep\":\"只写一句话\",\"usedMessageIds\":["+opinion.getId()+"]}");
        var request=new GoalReflectRequest(entry.id(),((Number)updated.get("version")).longValue());
        var result=coach.reflect("alice",id,request);assertEquals(true,result.get("available"));
        var goal=(Map<?,?>)result.get("goal");assertEquals("active",goal.get("status"));assertEquals("写出一个段落",goal.get("completionCriteria"));
        var actions=(List<GoalRequest.Action>)goal.get("actions");assertEquals(List.of("打开文档","只写一句话","投入五分钟"),actions.stream().map(GoalRequest.Action::title).toList());assertEquals("done",actions.get(0).status());
        assertEquals(result.get("goal"),coach.reflect("alice",id,request).get("goal"));verify(llm,times(1)).complete(anyString(),contains(opinion.getContent()));
        var report=weekly.overview("alice",0);assertEquals(1,report.get("completedSteps"));assertEquals(1,report.get("recordCount"));assertEquals(7,((List<?>)report.get("days")).size());
        assertThrows(org.springframework.web.server.ResponseStatusException.class,()->coach.reflect("alice",id+1,request));
    }

    @Test
    void partialOnlySuggestsAndUnavailableModelCannotEraseSavedCompletion() {
        var goal=goals.save("alice",null,new GoalRequest("写一段话","","active",null,true,List.of(new GoalRequest.Action("first","写一段话","planned",List.of()))));
        long id=((Number)goal.get("id")).longValue();
        var check=goals.checkIn("alice",id,new GoalCheckInRequest("first","partial","已写一句，但还不知道怎么继续",((Number)goal.get("version")).longValue(),true));
        when(llm.complete(anyString(),anyString())).thenReturn("{\"feedback\":\"已经写出一句话，继续可以再小一点。\",\"completionCriteria\":\"写完一段话\",\"goalCompleted\":true,\"completionReason\":\"缺少完整段落证据\",\"completedEvidenceIds\":[\"first\"],\"nextActionId\":\"\",\"nextStep\":\"为这句话补一个例子\",\"usedMessageIds\":[]}");
        var recorded=(Map<?,?>)check.get("goal");var entry=(GoalCheckInResponse)check.get("checkIn");
        var result=coach.reflect("alice",id,new GoalReflectRequest(entry.id(),((Number)recorded.get("version")).longValue()));
        assertEquals(recorded,result.get("goal"));assertEquals(false,((Map<?,?>)result.get("reflection")).get("goalCompleted"));
        var done=goals.checkIn("alice",id,new GoalCheckInRequest("first","done","写完了",((Number)recorded.get("version")).longValue(),true));
        when(llm.complete(anyString(),anyString())).thenThrow(new RuntimeException("offline"));
        var finished=(Map<?,?>)done.get("goal");var doneEntry=(GoalCheckInResponse)done.get("checkIn");
        assertEquals(false,coach.reflect("alice",id,new GoalReflectRequest(doneEntry.id(),((Number)finished.get("version")).longValue())).get("available"));
        assertEquals("done",((List<GoalRequest.Action>)goals.get("alice",id).get("actions")).get(0).status());
    }

    @Test
    void createsOrderedOwnedStepsOnlyFromExplicitSavedCommandAndNeverDuplicates() {
        memory.rememberAgentTurn(new AgentTurnPersistRequest("create-1","alice",session.getId(),
                "帮我创建一个目标，让拖延的工作开始，先花五分钟就好。","可以拆成小步骤。","","","",Map.of()));
        when(llm.complete(anyString(),anyString())).thenReturn("{\"title\":\"让工作开始\",\"description\":\"先投入五分钟\",\"steps\":[\"打开文档\",\"写下一个标题\",\"投入五分钟\"]}");
        var request=new GoalCreateRequest("alice","create-1",session.getId());
        assertThrows(org.springframework.web.server.ResponseStatusException.class,
                ()->creation.createFromTurn(new GoalCreateRequest("alice","create-1",session.getId()+1)));
        var result=creation.createFromTurn(request);
        assertEquals(true,result.get("created"));
        var goal=(Map<?,?>)result.get("goal");
        var steps=(List<GoalRequest.Action>)goal.get("actions");
        assertEquals(List.of("打开文档","写下一个标题","投入五分钟"),steps.stream().map(GoalRequest.Action::title).toList());
        assertTrue(steps.stream().allMatch(a->a.status().equals("planned")&&a.feedback().isEmpty()));
        assertEquals(goal,creation.createFromTurn(request).get("goal"));
        assertEquals(1,goals.list("alice").size());verify(llm,times(1)).complete(anyString(),anyString());
        goals.delete("alice",((Number)goal.get("id")).longValue());
        assertEquals("deleted",creation.createFromTurn(request).get("reason"));
        assertTrue(goals.list("alice").isEmpty());
    }

    @Test
    void wishesQuestionsNegationAndInvalidPlansDoNotCreateGoals() {
        for(String text:List.of("我想早点休息。","不要帮我创建目标。","如何创建目标？","他说帮我创建目标。")) {
            String id=UUID.randomUUID().toString();
            memory.rememberAgentTurn(new AgentTurnPersistRequest(id,"alice",session.getId(),text,"收到","","","",Map.of()));
            assertEquals(false,creation.createFromTurn(new GoalCreateRequest("alice",id,session.getId())).get("created"));
        }
        verifyNoInteractions(llm);
        memory.rememberAgentTurn(new AgentTurnPersistRequest("invalid-plan","alice",session.getId(),"帮我创建一个休息目标","收到","","","",Map.of()));
        when(llm.complete(anyString(),anyString())).thenReturn("{\"title\":\"休息\",\"description\":\"\",\"steps\":[\"一步\",\"一步\"]}");
        assertEquals(false,creation.createFromTurn(new GoalCreateRequest("alice","invalid-plan",session.getId())).get("created"));
        assertTrue(goals.list("alice").isEmpty());
        assertNull(receipts.findByUserAndRequestId(alice,"invalid-plan").orElseThrow().getCreatedGoalId());
        assertThrows(org.springframework.web.server.ResponseStatusException.class,
                ()->creation.createFromTurn(new GoalCreateRequest("alice","missing",session.getId())));
    }

    @Test
    void overviewRepositoryQueriesRespectOwnerWindowAndUserRole() {
        var expression = new ConversationMessage();
        expression.setUser(alice); expression.setSession(session); expression.setRole("user");
        expression.setContent("自己的近期表达"); expression.setCreatedAt(LocalDateTime.now());
        messages.saveAndFlush(expression);
        var assistant = new ConversationMessage();
        assistant.setUser(alice); assistant.setSession(session); assistant.setRole("assistant");
        assistant.setContent("助手的建议不是实际经历"); assistant.setCreatedAt(LocalDateTime.now());
        messages.saveAndFlush(assistant);
        var other = new User(); other.setUsername("other"); other.setPassword("test-only"); other.setRole("USER");
        other = users.saveAndFlush(other);
        var privateSession = new ConversationSession(); privateSession.setUser(other); privateSession.setTitle("私密对话");
        privateSession.setCreatedAt(LocalDateTime.now()); privateSession.setUpdatedAt(LocalDateTime.now());
        sessions.saveAndFlush(privateSession);
        var result = new ConversationOverviewService(users, sessions, messages, llm,
                new ObjectMapper().findAndRegisterModules()).overview("alice",30,false);
        assertEquals("limited",result.status()); assertEquals(1,result.coverage().conversationCount());
        assertEquals(1,result.evidence().size()); assertEquals("自己的近期表达",result.evidence().get(0).text());
        verifyNoInteractions(llm);
    }

    @Test
    void realDatabaseStoresOneTurnAndOnePendingMemoryJobOnRetry() {
        var request =
                new AgentTurnPersistRequest(
                        "turn-1", "alice", session.getId(), "想休息", "收到", "", "", "", Map.of());
        memory.rememberAgentTurn(request);
        memory.rememberAgentTurn(request);
        assertEquals(2, messages.count());
        assertEquals(1, receipts.count());
        assertEquals(
                1,
                receipts.findByMemoryStatusOrderByIdAsc("PENDING", PageRequest.of(0, 20)).size());
        verifyNoInteractions(llm);
    }

    @Test
    void goalWithActionAndFeedbackRoundTripsThroughJpa() {
        var request =
                new GoalRequest(
                        "给自己一点休息",
                        "",
                        "active",
                        null,
                        true,
                        List.of(new GoalRequest.Action("a1", "尝试散步", "done", List.of("感觉放松一些"))));
        var created = goals.save("alice", null, request);
        assertNotNull(created.get("version"));
        assertEquals(1, goals.list("alice").size());
        assertTrue(goals.list("alice").get(0).get("actions").toString().contains("感觉放松一些"));
        goals.delete("alice", (Long) created.get("id"));
        assertTrue(goals.list("alice").isEmpty());
    }
    Map<String,Object> plannedGoal() {
        return goals.save("alice",null,new GoalRequest("给自己休息","","active",null,true,
                List.of(new GoalRequest.Action("walk","散步十分钟","planned",List.of()))));
    }
    @Test void quickCheckInsPersistPartialAndRestWithoutClaimingCompletion() {
        var created=plannedGoal();Long id=(Long)created.get("id");
        var partial=goals.checkIn("alice",id,new GoalCheckInRequest("walk","partial","先走了两分钟",(Long)created.get("version"),true));
        var updated=(Map<String,Object>)partial.get("goal");
        assertTrue(updated.get("actions").toString().contains("status=planned"));
        var rest=goals.checkIn("alice",id,new GoalCheckInRequest("walk","rest","",(Long)updated.get("version"),true));
        updated=(Map<String,Object>)rest.get("goal");
        assertTrue(updated.get("actions").toString().contains("status=planned"));
        assertEquals(2,goals.checkIns("alice").size());
        var other=new User();other.setUsername("bob");other.setPassword("test-only");other.setRole("USER");users.saveAndFlush(other);
        assertTrue(goals.checkIns("bob").isEmpty());
        assertThrows(org.springframework.web.server.ResponseStatusException.class,()->goals.checkIn("bob",id,new GoalCheckInRequest("walk","done","",0L,true)));
        assertThrows(org.springframework.web.server.ResponseStatusException.class,()->goals.checkIn("alice",id,new GoalCheckInRequest("walk","done","",(Long)created.get("version"),true)));
        goals.delete("alice",id);assertTrue(goals.checkIns("alice").isEmpty());
    }
    @Test void conversationProgressRequiresSavedOwnedEvidenceAndIsReplaySafeAndReversible() {
        var created=plannedGoal();Long id=(Long)created.get("id"),version=(Long)created.get("version");
        String text="我已经散步十分钟了。";
        var request=new GoalSyncRequest("alice","progress-turn",session.getId(),id,"walk",version,"done",text);
        assertThrows(org.springframework.web.server.ResponseStatusException.class,()->goals.syncFromTurn(request));
        memory.rememberAgentTurn(new AgentTurnPersistRequest("progress-turn","alice",session.getId(),text,"收到","","","",Map.of()));
        var result=goals.syncFromTurn(request);assertEquals(true,result.get("applied"));
        var entry=(GoalCheckInResponse)result.get("checkIn");assertEquals("conversation",entry.source());
        assertEquals(entry.id(),((GoalCheckInResponse)goals.syncFromTurn(request).get("checkIn")).id());
        assertEquals(1,goals.checkIns("alice").size());assertTrue(goals.list("alice").get(0).get("actions").toString().contains("status=done"));
        var undone=goals.undoSync("alice",entry.id());assertTrue(((GoalCheckInResponse)undone.get("checkIn")).undone());
        assertTrue(goals.list("alice").get(0).get("actions").toString().contains("status=planned"));
        assertEquals(false,goals.syncFromTurn(request).get("applied"));
        assertThrows(org.springframework.web.server.ResponseStatusException.class,()->goals.syncFromTurn(new GoalSyncRequest("alice","progress-turn",session.getId(),id,"walk",version,"done","助手的建议")));
        goals.setAutoSync("alice",false);assertEquals(false,goals.syncFromTurn(request).get("applied"));
    }
    @Test void actualPartialProgressAllowsUnfinishedStepAndCanBeUndone() {
        var created=plannedGoal();Long id=(Long)created.get("id");String text="我刚才已经散步了两分钟，十分钟还没完成。";
        memory.rememberAgentTurn(new AgentTurnPersistRequest("partial-turn","alice",session.getId(),text,"收到","","","",Map.of()));
        var result=goals.syncFromTurn(new GoalSyncRequest("alice","partial-turn",session.getId(),id,"walk",(Long)created.get("version"),"partial",text));
        assertEquals(true,result.get("applied"));assertTrue(goals.list("alice").get(0).get("actions").toString().contains("status=planned"));
        var entry=(GoalCheckInResponse)result.get("checkIn");assertEquals("partial",entry.outcome());
        assertTrue(((GoalCheckInResponse)goals.undoSync("alice",entry.id()).get("checkIn")).undone());
    }
    @Test void quotedOwnedGoalTitleDoesNotBecomeFutureIntent() {
        var created=goals.save("alice",null,new GoalRequest("准备工作汇报","","active",null,true,List.of(new GoalRequest.Action("write","写标题","planned",List.of()))));
        Long id=(Long)created.get("id");String text="我想聊聊目标「准备工作汇报」的进展：我刚才已经写标题了，这一步做完了。";
        memory.rememberAgentTurn(new AgentTurnPersistRequest("quoted-goal","alice",session.getId(),text,"收到","","","",Map.of()));
        var result=goals.syncFromTurn(new GoalSyncRequest("alice","quoted-goal",session.getId(),id,"write",(Long)created.get("version"),"done",text));
        assertEquals(true,result.get("applied"));
    }
    @Test void futurePlansNeverBecomeCompletedProgress() {
        var created=plannedGoal();Long id=(Long)created.get("id");String text="我打算明天散步十分钟。";
        memory.rememberAgentTurn(new AgentTurnPersistRequest("future-turn","alice",session.getId(),text,"收到","","","",Map.of()));
        var result=goals.syncFromTurn(new GoalSyncRequest("alice","future-turn",session.getId(),id,"walk",(Long)created.get("version"),"done",text));
        assertEquals(false,result.get("applied"));assertTrue(goals.checkIns("alice").isEmpty());
    }

}
