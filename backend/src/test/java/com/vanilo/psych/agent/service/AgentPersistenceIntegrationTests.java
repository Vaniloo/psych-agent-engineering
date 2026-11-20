package com.vanilo.psych.agent.service;

import static org.junit.jupiter.api.Assertions.*;
import static org.mockito.Mockito.*;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.vanilo.psych.agent.dto.*;
import com.vanilo.psych.agent.entity.*;
import com.vanilo.psych.agent.repository.*;

import org.junit.jupiter.api.*;
import org.mybatis.spring.boot.autoconfigure.MybatisAutoConfiguration;
import org.springframework.boot.autoconfigure.ImportAutoConfiguration;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.dao.OptimisticLockingFailureException;
import org.springframework.test.context.transaction.TestTransaction;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.autoconfigure.jdbc.AutoConfigureTestDatabase;
import org.springframework.boot.test.autoconfigure.jdbc.JdbcTest;
import org.springframework.context.annotation.Import;
import org.springframework.data.redis.core.StringRedisTemplate;
import org.springframework.test.context.bean.override.mockito.MockitoBean;

import java.time.LocalDateTime;
import java.util.*;

@ImportAutoConfiguration(MybatisAutoConfiguration.class)
@JdbcTest(
        properties = {
            "spring.datasource.url=${ATTUNE_TEST_JDBC_URL:jdbc:h2:mem:agent;MODE=MySQL;DB_CLOSE_DELAY=-1}",
            "spring.datasource.username=${ATTUNE_TEST_DB_USER:sa}",
            "spring.datasource.password=${ATTUNE_TEST_DB_PASSWORD:}"
        })
@AutoConfigureTestDatabase(replace = AutoConfigureTestDatabase.Replace.NONE)
@Import({ConversationMemoryService.class, GoalService.class, GoalCreationService.class, GoalCoachService.class, GoalWeeklyService.class, AgentMetricsService.class, ObjectMapper.class})
class AgentPersistenceIntegrationTests {
    @MockitoBean LlmService llm;
    @MockitoBean RoleCardService roles;
    @MockitoBean UserProfileService profiles;
    @MockitoBean StringRedisTemplate redis;
    @Autowired JdbcTemplate jdbc;
    @Autowired KnowledgeDocumentMapper documents;
    @Autowired UserModelApiMapper settings;
    @Autowired RiskAlertEventMapper alerts;
    @Autowired SupportGoalMapper goalMapper;
    @Autowired UserMapper users;
    @Autowired ConversationSessionMapper sessions;
    @Autowired ConversationMessageMapper messages;
    @Autowired UserProfileMapper profileRecords;
    @Autowired AgentTurnReceiptMapper receipts;
    @Autowired ConversationMemoryService memory;
    @Autowired GoalService goals;
    @Autowired GoalCreationService creation;
    @Autowired GoalCoachService coach;
    @Autowired GoalWeeklyService weekly;
    @Autowired AgentMetricsService metrics;
    @Autowired PsychologicalReportMapper psychologicalReports;
    User alice;
    ConversationSession session;

    @BeforeEach
    void setup() {
        alice = new User();
        alice.setUsername("alice");
        alice.setPassword("test-only");
        alice.setRole("USER");
        users.insert(alice);
        session = new ConversationSession();
        session.setUser(alice);
        session.setTitle("测试");
        session.setCreatedAt(LocalDateTime.now());
        session.setUpdatedAt(LocalDateTime.now());
        sessions.insert(session);
        UserProfile profile = new UserProfile();
        profile.setUser(alice);
        profile.setUpdatedAt(LocalDateTime.now());
        profileRecords.insert(profile);
        when(profiles.getOrCreateProfile(alice)).thenReturn(profile);
    }

    @Test
    void recentEvidenceIsBoundedUserOnlyOwnedAndAvailableBeforeOutboxAcknowledgement() {
        for (int i = 0; i < 15; i++) {
            memory.rememberAgentTurn(new AgentTurnPersistRequest("recent-" + i, "alice", session.getId(),
                    "今天我的情绪记录" + i, "助手内容不能证明用户情绪", "", "", "", Map.of()));
        }
        var other = new User(); other.setUsername("another-memory-owner"); other.setPassword("test-only"); other.setRole("USER");
        users.insert(other);
        var otherSession = new ConversationSession(); otherSession.setUser(other); otherSession.setTitle("其他用户");
        otherSession.setCreatedAt(LocalDateTime.now()); otherSession.setUpdatedAt(LocalDateTime.now()); sessions.insert(otherSession);
        messages.insert(new ConversationMessage(null, other, otherSession, "user", "OTHER_OWNER_PRIVATE", LocalDateTime.now()));
        var found = memory.recentUserEvidence("alice");
        assertEquals(15, found.size());
        assertTrue(found.stream().allMatch(r -> "user".equals(r.get("role"))));
        assertFalse(found.toString().contains("OTHER_OWNER_PRIVATE"));
        assertFalse(found.toString().contains("助手内容"));
        assertEquals("今天我的情绪记录14", found.get(0).get("content"));
        assertDoesNotThrow(() -> java.time.OffsetDateTime.parse((String) found.get(0).get("observedAt")));
        assertEquals(15, receipts.memoryJobs("PENDING", 20).size());
        for (int i = 15; i < 35; i++) {
            memory.rememberAgentTurn(new AgentTurnPersistRequest("recent-" + i, "alice", session.getId(),
                    "今天我的情绪记录" + i, "助手内容", "", "", "", Map.of()));
        }
        assertEquals(24, memory.recentUserEvidence("alice").size());
    }

    @Test
    void memoryReceiptLookupRequiresTheSavedOwnedTurn() {
        assertThrows(org.springframework.web.server.ResponseStatusException.class,
                () -> memory.requireSavedTurn("alice", "missing"));
        memory.beginAgentTurn(new AgentTurnBeginRequest("not-yet-saved", "alice", session.getId(), "你好"));
        assertThrows(org.springframework.web.server.ResponseStatusException.class,
                () -> memory.requireSavedTurn("alice", "not-yet-saved"));
        memory.rememberAgentTurn(new AgentTurnPersistRequest("saved-receipt", "alice", session.getId(),
                "我喜欢音乐", "收到", "", "", "", Map.of()));
        assertDoesNotThrow(() -> memory.requireSavedTurn("alice", "saved-receipt"));
        var other = new User(); other.setUsername("other-status-owner"); other.setPassword("test"); other.setRole("USER");
        users.insert(other);
        assertThrows(org.springframework.web.server.ResponseStatusException.class,
                () -> memory.requireSavedTurn("other-status-owner", "saved-receipt"));
    }

    @Test
    void acceptedCommandSupportsGuardedGoalBeforeReplyAndOnlyFinalTurnQueuesMemory() {
        var begin = new AgentTurnBeginRequest("loop-create", "alice", session.getId(), "帮我创建一个目标，写一句话。");
        assertEquals(false, memory.beginAgentTurn(begin).get("completed"));
        assertEquals(false, memory.beginAgentTurn(begin).get("completed"));
        assertEquals(0, messageCount());
        assertEquals(0, receipts.memoryJobs("PENDING", 20).size());
        when(llm.complete(anyString(), anyString())).thenReturn("{\"title\":\"写一句话\",\"description\":\"写下想法\",\"steps\":[\"打开笔记写一个句子\"]}");
        var create = new GoalCreateRequest("alice", "loop-create", session.getId());
        var created = creation.createFromTurn(create);
        assertEquals(true, created.get("created"));
        assertEquals(true, creation.createFromTurn(create).get("replayed"));
        assertEquals(0, messageCount());
        var finish = new AgentTurnPersistRequest("loop-create", "alice", session.getId(), begin.userMessage(), "目标已保存。", "", "", "", Map.of());
        memory.rememberAgentTurn(finish);
        memory.rememberAgentTurn(finish);
        assertEquals(2, messageCount());
        var receipt = receipts.findByUserAndRequestId(alice, "loop-create").orElseThrow();
        assertEquals("PENDING", receipt.getMemoryStatus());
        assertEquals(((Map<?, ?>) created.get("goal")).get("id"), receipt.getCreatedGoalId());
        assertEquals(true, memory.beginAgentTurn(begin).get("completed"));
        assertEquals("目标已保存。", memory.beginAgentTurn(begin).get("assistantReply"));
        assertEquals(1, goals.list("alice").size());
        verify(llm, times(1)).complete(anyString(), anyString());
    }

    @Test
    void acceptedCommandCannotBeReboundToDifferentContentOrAnotherSession() {
        memory.beginAgentTurn(new AgentTurnBeginRequest("loop-owned", "alice", session.getId(), "创建一个休息目标"));
        assertThrows(org.springframework.web.server.ResponseStatusException.class, () -> memory.beginAgentTurn(
                new AgentTurnBeginRequest("loop-owned", "alice", session.getId(), "创建一个不同目标")));
        assertThrows(org.springframework.web.server.ResponseStatusException.class, () -> memory.rememberAgentTurn(
                new AgentTurnPersistRequest("loop-owned", "alice", session.getId(), "伪造原话", "回复", "", "", "", Map.of())));
        assertEquals(0, messageCount());
    }

    @Test
    void memoryAcknowledgementPreservesGoalCreationAndItsRetryProtection() {
        memory.rememberAgentTurn(new AgentTurnPersistRequest("concurrent-create", "alice", session.getId(),
                "帮我创建一个目标，写一句话。", "可以从一句开始。", "", "", "", Map.of()));
        var receiptId = receipts.findByUserAndRequestId(alice, "concurrent-create").orElseThrow().getId();
        when(llm.complete(anyString(), anyString())).thenReturn("{\"title\":\"写一句话\",\"description\":\"表达自己的想法\",\"steps\":[\"写下一个句子\"]}");
        var request = new GoalCreateRequest("alice", "concurrent-create", session.getId());
        var created = creation.createFromTurn(request);
        assertEquals(1, receipts.completeMemoryJob(receiptId));
        var acknowledged = receipts.findById(receiptId).orElseThrow();
        assertEquals("DONE", acknowledged.getMemoryStatus());
        assertEquals(((Map<?, ?>) created.get("goal")).get("id"), acknowledged.getCreatedGoalId());
        assertEquals(1L, ((Map<?, ?>) metrics.summary("alice", 7).get("goalActivity")).get("agentCreated"));
        assertEquals(true, creation.createFromTurn(request).get("replayed"));
        assertEquals(1, goals.list("alice").size());
        verify(llm, times(1)).complete(anyString(), anyString());
        assertEquals(1, receipts.completeMemoryJob(receiptId));
        assertEquals(0, receipts.completeMemoryJob(-1L));
    }

    @Test
    void trendQueryUsesOwnedRowsAndBoundCutoffOnTheRealDatabase() {
        var now = LocalDateTime.now();
        var other = new User();
        other.setUsername("other-report-owner"); other.setPassword("test-only"); other.setRole("USER");
        users.insert(other);
        psychologicalReports.insert(new PsychologicalReport(null, "近期原话", "low", "平静", 0.5, now, alice));
        psychologicalReports.insert(new PsychologicalReport(null, "超出窗口", "low", "平静", 0.5, now.minusDays(45), alice));
        psychologicalReports.insert(new PsychologicalReport(null, "其他用户", "low", "平静", 0.5, now, other));
        var reports = psychologicalReports.findByUser(alice.getId());
        assertEquals(2, reports.size());
        assertTrue(reports.stream().allMatch(report -> "alice".equals(report.getUsername())));
        assertEquals(reports.get(0), psychologicalReports.findById(reports.get(0).getId()).orElseThrow());

        var owned = psychologicalReports.findEmotionTrendByUserId(alice.getId(), now.minusDays(30));
        assertEquals(1, owned.size());
        assertEquals(1L, owned.get(0).getCount());
        var all = psychologicalReports.findEmotionTrend(now.minusDays(30));
        assertEquals(2L, all.get(0).getCount());
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
        var other=new User();other.setUsername("bob");other.setPassword("test-only");other.setRole("USER");users.insert(other);
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
        var opinion=new ConversationMessage();opinion.setUser(alice);opinion.setSession(session);opinion.setRole("user");opinion.setContent("五分钟还是有点多，我想先写一句话。");opinion.setCreatedAt(LocalDateTime.now());messages.insert(opinion);
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
    void finishedGoalRemainsReadableBeyondRecentFiftyAndGroundsNextGoal() throws Exception {
        var source=goals.save("alice",null,new GoalRequest("准备Agent面试","练习一题","completed",null,true,
                List.of(new GoalRequest.Action("a","说出工具权限控制","done",List.of("第一遍紧张，第二遍更顺了"))),"完成一题完整回答"));
        for(int i=0;i<51;i++) goals.save("alice",null,new GoalRequest("其他目标"+i,"","active",null,true,
                List.of(new GoalRequest.Action("a","一步","planned",List.of())),""));
        assertTrue(goals.list("alice").stream().noneMatch(g->"准备Agent面试".equals(g.get("title"))));
        var found=goals.named("alice","准备Agent面试");
        assertEquals(1,found.size());assertEquals("completed",found.get(0).get("status"));
        assertEquals(1,goals.listByStatus("alice","completed").size());
        var other=new User();other.setUsername("other-goal-owner");other.setPassword("test-only");other.setRole("USER");users.insert(other);
        goals.save("other-goal-owner",null,new GoalRequest("准备Agent面试","OTHER_PRIVATE_FEEDBACK","completed",null,true,
                List.of(new GoalRequest.Action("a","别人的步骤","done",List.of())),""));
        memory.rememberAgentTurn(new AgentTurnPersistRequest("next-goal","alice",session.getId(),
                "你看看能不能设置一个新的下一步目标","准备按你给的方向创建。","","","",Map.of()));
        when(llm.complete(anyString(),anyString())).thenAnswer(call->{
            String input=call.getArgument(1);
            assertTrue(input.contains("第一遍紧张，第二遍更顺了"));assertTrue(input.contains("完成一题完整回答"));
            assertFalse(input.contains("OTHER_PRIVATE_FEEDBACK"));
            return "{\"title\":\"练习下一类Agent面试题\",\"description\":\"扩展练习\",\"completionCriteria\":\"完整回答一题新类型\",\"steps\":[\"选一道记忆设计题\",\"完整回答并回顾\"]}";
        });
        var request=new GoalCreateRequest("alice","next-goal",session.getId(),"准备Agent面试");
        var result=creation.createFromTurn(request);assertEquals(true,result.get("created"));
        var next=(Map<?,?>)result.get("goal");assertEquals("active",next.get("status"));
        assertTrue(((List<GoalRequest.Action>)next.get("actions")).stream().allMatch(a->"planned".equals(a.status())));
        assertEquals("completed",goals.get("alice",((Number)source.get("id")).longValue()).get("status"));
        assertEquals(true,creation.createFromTurn(request).get("replayed"));verify(llm,times(1)).complete(anyString(),anyString());
    }

    @Test
    void resolvesGoalReferenceFromOnlyOwnedSameSessionConversationAndReplaysOnce() {
        memory.rememberAgentTurn(new AgentTurnPersistRequest("sleep-source","alice",session.getId(),
                "我想每天睡够八小时，先聊聊，不要保存。","可以把每天睡够八小时作为一个目标。","","","",Map.of()));
        var unrelated=new ConversationSession();unrelated.setUser(alice);unrelated.setTitle("其他会话");
        unrelated.setCreatedAt(LocalDateTime.now());unrelated.setUpdatedAt(LocalDateTime.now());sessions.insert(unrelated);
        memory.rememberAgentTurn(new AgentTurnPersistRequest("private-other-session","alice",unrelated.getId(),
                "OTHER_SESSION_PRIVATE_GOAL","不相关建议","","","",Map.of()));
        memory.rememberAgentTurn(new AgentTurnPersistRequest("sleep-ref","alice",session.getId(),
                "那把这个设成新目标吧","准备创建，尚未保存。","","","",Map.of()));
        when(llm.complete(anyString(),anyString())).thenAnswer(call->{
            String input=call.getArgument(1);
            assertTrue(input.contains("每天睡够八小时"));assertTrue(input.contains("recentConversation"));
            assertTrue(input.contains("那把这个设成新目标吧"));
            assertFalse(input.contains("准备创建，尚未保存"));assertFalse(input.contains("OTHER_SESSION_PRIVATE_GOAL"));
            return "{\"title\":\"每天睡够八小时\",\"description\":\"改善睡眠\",\"completionCriteria\":\"连续一周记录八小时睡眠\",\"steps\":[\"选择上床时间\",\"记录实际睡眠时长\"]}";
        });
        var request=new GoalCreateRequest("alice","sleep-ref",session.getId());
        var result=creation.createFromTurn(request);assertEquals(true,result.get("created"));
        assertEquals("每天睡够八小时",((Map<?,?>)result.get("goal")).get("title"));
        assertEquals(true,creation.createFromTurn(request).get("replayed"));verify(llm,times(1)).complete(anyString(),anyString());
    }

    @Test
    void referenceWithoutOwnedContextDoesNotGenerateGoal() {
        memory.rememberAgentTurn(new AgentTurnPersistRequest("empty-ref","alice",session.getId(),
                "那把这个设成新目标吧","你想创建哪个目标？","","","",Map.of()));
        assertEquals("unresolved_reference",creation.createFromTurn(new GoalCreateRequest("alice","empty-ref",session.getId())).get("reason"));
        verifyNoInteractions(llm);assertTrue(goals.list("alice").isEmpty());
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
        messages.insert(expression);
        var assistant = new ConversationMessage();
        assistant.setUser(alice); assistant.setSession(session); assistant.setRole("assistant");
        assistant.setContent("助手的建议不是实际经历"); assistant.setCreatedAt(LocalDateTime.now());
        messages.insert(assistant);
        var other = new User(); other.setUsername("other"); other.setPassword("test-only"); other.setRole("USER");
        users.insert(other);
        var privateSession = new ConversationSession(); privateSession.setUser(other); privateSession.setTitle("私密对话");
        privateSession.setCreatedAt(LocalDateTime.now()); privateSession.setUpdatedAt(LocalDateTime.now());
        sessions.insert(privateSession);
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
        assertEquals(2, messageCount());
        assertEquals(1, receipts.count());
        assertEquals(
                1,
                receipts.memoryJobs("PENDING", 20).size());
        verifyNoInteractions(llm);
    }

    @Test
    void replacingKnowledgeAndApiSettingsKeepsTheirOriginalKeys() {
        var document = new KnowledgeDocument("test-doc", "初稿", "demo", "integration", LocalDateTime.now());
        documents.upsert(document);
        document.setContent("修订稿");
        documents.upsert(document);
        assertEquals(List.of("修订稿"), documents.findBySource("integration").stream()
                .map(KnowledgeDocument::getContent).toList());
        assertTrue(documents.page(org.springframework.data.domain.PageRequest.of(0, 10)).stream()
                .anyMatch(row -> row.getId().equals("test-doc")));

        var config = new UserModelApi();
        config.setUserId(alice.getId());
        config.setBaseUrl("https://example.test");
        config.setModel("first");
        config.setEncryptedKey("test-ciphertext");
        settings.upsert(config);
        config.setModel("second");
        settings.upsert(config);
        assertEquals("second", settings.findById(alice.getId()).orElseThrow().getModel());
        settings.deleteById(alice.getId());
        assertTrue(settings.findById(alice.getId()).isEmpty());
    }

    @Test
    void evaluationMetricsPreserveLegacyColumnNamesAndAlertRetriesSeeStateChanges() {
        var evaluation = new ObjectMapper().createObjectNode()
                .put("datasetVersion", "integration").put("nDcgAt5", 0.75).put("p95LatencyMs", 110);
        metrics.recordEvaluation(evaluation);
        assertEquals(0.75, metrics.latestEvaluation().get("nDcgAt5"));
        assertEquals(110.0, metrics.latestEvaluation().get("p95LatencyMs"));

        var now = LocalDateTime.now();
        var event = new RiskAlertEvent(null, null, "alice", "test", "high", "test", 0.9,
                "PENDING", 0, null, now, now);
        alerts.insert(event);
        assertEquals(event.getId(), alerts.retryable("PENDING", 3).get(0).getId());
        event.setStatus("SENT");
        alerts.update(event);
        assertTrue(alerts.retryable("PENDING", 3).isEmpty());
    }

    private long messageCount() {
        return jdbc.queryForObject("SELECT COUNT(*) FROM conversation_messages", Long.class);
    }

    @Test
    void rollingBackATurnRemovesBothMessagesAndItsPendingMemoryReceipt() {
        memory.rememberAgentTurn(new AgentTurnPersistRequest(
                "rollback-turn", "alice", session.getId(), "想休息", "收到", "", "", "", Map.of()));
        assertEquals(2, messageCount());
        assertEquals(1, receipts.count());

        TestTransaction.flagForRollback();
        TestTransaction.end();

        assertEquals(0, messageCount());
        assertEquals(0, receipts.count());
    }

    @Test
    void staleGoalCannotOverwriteOrDeleteANewerRevision() {
        Long id = (Long) plannedGoal().get("id");
        var current = goalMapper.findByIdAndUser(id, alice).orElseThrow();
        var stale = goalMapper.findByIdAndUser(id, alice).orElseThrow();
        current.setTitle("新的目标");
        goalMapper.save(current);

        assertThrows(OptimisticLockingFailureException.class, () -> goalMapper.save(stale));
        assertThrows(OptimisticLockingFailureException.class, () -> goalMapper.delete(stale));
        var saved = goalMapper.findByIdAndUser(id, alice).orElseThrow();
        assertEquals("新的目标", saved.getTitle());
        assertEquals(stale.getVersion() + 1, saved.getVersion());
    }

    @Test
    void goalWithActionAndFeedbackRoundTripsThroughMyBatis() {
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
    @Test void conversationDoneAdvancesOnceUsingCurrentFeelingsAndUndoRestoresTheWholeChange() {
        var created=goals.save("alice",null,new GoalRequest("每天散步","","active",null,true,
                List.of(new GoalRequest.Action("walk","散步十分钟","planned",List.of())),"连续一周留下散步记录"));
        Long id=(Long)created.get("id");String text="我已经散步十分钟了，今天有点累。";
        messages.insert(new ConversationMessage(null,alice,session,"user","下一次想短一点",LocalDateTime.now()));
        memory.rememberAgentTurn(new AgentTurnPersistRequest("advance-daily","alice",session.getId(),text,"已记录","","","",Map.of()));
        var synced=goals.syncFromTurn(new GoalSyncRequest("alice","advance-daily",session.getId(),id,"walk",0L,"done",text));
        assertEquals(true,synced.get("applied"));var entry=(GoalCheckInResponse)synced.get("checkIn");
        when(llm.complete(anyString(),anyString())).thenReturn("{\"feedback\":\"走完了也感到累，下一次缩小一点。\",\"completionCriteria\":\"不改原标准\",\"goalCompleted\":false,\"completionReason\":\"只有一次，尚未形成一周记录\",\"completedEvidenceIds\":[\"walk\"],\"nextActionId\":\"\",\"nextStep\":\"明天饭后散步两分钟，记录感受\",\"usedMessageIds\":[]}");
        var request=new GoalAdvanceRequest("alice","advance-daily",session.getId(),id,entry.id());
        var result=coach.advanceFromTurn(request);assertEquals(true,result.get("available"));
        var goal=(Map<?,?>)result.get("goal");assertEquals("active",goal.get("status"));
        var actions=(List<GoalRequest.Action>)goal.get("actions");assertEquals(2,actions.size());
        assertEquals("done",actions.get(0).status());assertEquals("planned",actions.get(1).status());
        assertEquals("连续一周留下散步记录",goal.get("completionCriteria"));
        var replay=coach.advanceFromTurn(request);assertEquals(goal,replay.get("goal"));
        assertFalse(((Map<?,?>)replay.get("reflection")).containsKey("_undo"));
        verify(llm,times(1)).complete(anyString(),argThat(input->input.contains(text)&&input.contains("下一次想短一点")&&input.contains("recordedAt")&&input.contains("currentUserMessage")));
        assertEquals(id,goals.conversationGoalId("alice",session.getId(),null));
        var undone=goals.undoSync("alice",entry.id());var restored=(Map<?,?>)undone.get("goal");
        var restoredActions=(List<GoalRequest.Action>)restored.get("actions");assertEquals(1,restoredActions.size());
        assertEquals("planned",restoredActions.get(0).status());assertTrue(restoredActions.get(0).feedback().isEmpty());
        assertThrows(org.springframework.web.server.ResponseStatusException.class,()->coach.advanceFromTurn(request));
    }

    @Test void conversationOneOffCompletionIsOwnedAndCanBeUndoneAfterCoaching() {
        var created=goals.save("alice",null,new GoalRequest("完成一次散步","","active",null,true,
                List.of(new GoalRequest.Action("walk","散步十分钟","planned",List.of())),"完成一次十分钟散步"));
        Long id=(Long)created.get("id");String text="我已经散步十分钟了。";
        memory.rememberAgentTurn(new AgentTurnPersistRequest("advance-once","alice",session.getId(),text,"已记录","","","",Map.of()));
        var synced=goals.syncFromTurn(new GoalSyncRequest("alice","advance-once",session.getId(),id,"walk",0L,"done",text));
        var entry=(GoalCheckInResponse)synced.get("checkIn");
        when(llm.complete(anyString(),anyString())).thenReturn("{\"feedback\":\"这次十分钟散步已经完成。\",\"completionCriteria\":\"完成一次十分钟散步\",\"goalCompleted\":true,\"completionReason\":\"已完成唯一的小步骤，符合一次散步标准\",\"completedEvidenceIds\":[\"walk\"],\"nextActionId\":\"\",\"nextStep\":\"\",\"usedMessageIds\":[]}");
        assertThrows(org.springframework.web.server.ResponseStatusException.class,()->coach.advanceFromTurn(
                new GoalAdvanceRequest("alice","advance-once",session.getId()+1,id,entry.id())));
        var other=new User();other.setUsername("bob");other.setPassword("test-only");other.setRole("USER");users.insert(other);
        assertThrows(org.springframework.web.server.ResponseStatusException.class,()->coach.advanceFromTurn(
                new GoalAdvanceRequest("bob","advance-once",session.getId(),id,entry.id())));
        assertEquals("completed",((Map<?,?>)coach.advanceFromTurn(new GoalAdvanceRequest("alice","advance-once",session.getId(),id,entry.id())).get("goal")).get("status"));
        var undone=(Map<?,?>)goals.undoSync("alice",entry.id()).get("goal");assertEquals("active",undone.get("status"));
        assertEquals("planned",((List<GoalRequest.Action>)undone.get("actions")).get(0).status());
    }

    @Test void conversationPlanningFailureKeepsCompletionAndStaleVersionCannotAppendANextStep() {
        var created=plannedGoal();Long id=(Long)created.get("id");String text="我已经散步十分钟了。";
        memory.rememberAgentTurn(new AgentTurnPersistRequest("advance-failure","alice",session.getId(),text,"已记录","","","",Map.of()));
        var synced=goals.syncFromTurn(new GoalSyncRequest("alice","advance-failure",session.getId(),id,"walk",0L,"done",text));
        var entry=(GoalCheckInResponse)synced.get("checkIn");var request=new GoalAdvanceRequest("alice","advance-failure",session.getId(),id,entry.id());
        when(llm.complete(anyString(),anyString())).thenThrow(new RuntimeException("offline"));
        assertEquals(false,coach.advanceFromTurn(request).get("available"));
        assertEquals("done",((List<GoalRequest.Action>)goals.get("alice",id).get("actions")).get(0).status());
        var current=goals.get("alice",id);goals.save("alice",id,new GoalRequest("调整后的目标","","active",(Long)current.get("version"),true,
                (List<GoalRequest.Action>)current.get("actions")));
        assertThrows(org.springframework.web.server.ResponseStatusException.class,()->coach.advanceFromTurn(request));
        verify(llm,times(1)).complete(anyString(),anyString());
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
        var other=new User();other.setUsername("bob");other.setPassword("test-only");other.setRole("USER");users.insert(other);
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

    @Test void modelProofReplacesLexicalGuardsButRetainsOwnedReceiptVersionReplayAndUndo() throws Exception {
        String secret="test-only-goal-proof",sha="a".repeat(64),text="头一项终于搞定啦";
        org.springframework.test.util.ReflectionTestUtils.setField(goals,"modelApprovalSecret",secret);
        org.springframework.test.util.ReflectionTestUtils.setField(goals,"modelApprovalSha",sha);
        try {
            var created=plannedGoal();Long id=(Long)created.get("id"),version=(Long)created.get("version");
            memory.rememberAgentTurn(new AgentTurnPersistRequest("model-progress","alice",session.getId(),text,"收到","","","",Map.of()));
            var plain=new GoalSyncRequest("alice","model-progress",session.getId(),id,"walk",version,"done",text);
            assertEquals(false,goals.syncFromTurn(plain).get("applied"));
            String token=goalProof(secret,sha,"model-progress",text,"progress",id,"walk",version,"done");
            assertEquals(false,goals.syncFromTurn(new GoalSyncRequest("alice","model-progress",session.getId(),id,"walk",version,"partial",text,token)).get("applied"));
            goals.setAutoSync("alice",false);
            var request=new GoalSyncRequest("alice","model-progress",session.getId(),id,"walk",version,"done",text,token);
            assertEquals(false,goals.syncFromTurn(request).get("applied"));goals.setAutoSync("alice",true);
            var applied=goals.syncFromTurn(request);assertEquals(true,applied.get("applied"));
            var entry=(GoalCheckInResponse)applied.get("checkIn");
            assertEquals(entry.id(),((GoalCheckInResponse)goals.syncFromTurn(request).get("checkIn")).id());
            assertEquals(1,goals.checkIns("alice").size());
            goals.undoSync("alice",entry.id());assertEquals(false,goals.syncFromTurn(request).get("applied"));
            memory.rememberAgentTurn(new AgentTurnPersistRequest("stale-model","alice",session.getId(),text,"收到","","","",Map.of()));
            String stale=goalProof(secret,sha,"stale-model",text,"progress",id,"walk",version,"done");
            assertThrows(org.springframework.web.server.ResponseStatusException.class,()->goals.syncFromTurn(
                    new GoalSyncRequest("alice","stale-model",session.getId(),id,"walk",version,"done",text,stale)));
        } finally {
            org.springframework.test.util.ReflectionTestUtils.setField(goals,"modelApprovalSecret","");
            org.springframework.test.util.ReflectionTestUtils.setField(goals,"modelApprovalSha","");
        }
    }

    @Test void modelCreationProofAuthorizesContextualAdoptionWithoutLexicalCommandAndReplays() throws Exception {
        String secret="test-only-goal-proof",sha="a".repeat(64),text="就照上面的安排，帮我存一个吧";
        org.springframework.test.util.ReflectionTestUtils.setField(goals,"modelApprovalSecret",secret);
        org.springframework.test.util.ReflectionTestUtils.setField(goals,"modelApprovalSha",sha);
        try {
            memory.rememberAgentTurn(new AgentTurnPersistRequest("reading-advice","alice",session.getId(),"最近想读一点书", "可以每天读一页。","","","",Map.of()));
            memory.rememberAgentTurn(new AgentTurnPersistRequest("model-create","alice",session.getId(),text,"收到","","","",Map.of()));
            assertEquals(false,creation.createFromTurn(new GoalCreateRequest("alice","model-create",session.getId())).get("created"));
            when(llm.complete(anyString(),anyString())).thenReturn("{\"title\":\"读书\",\"description\":\"读一点\",\"steps\":[\"读一页\"]}");
            String token=goalProof(secret,sha,"model-create",text,"create",null,null,null,null);
            var request=new GoalCreateRequest("alice","model-create",session.getId(),null,token);
            assertEquals(true,creation.createFromTurn(request).get("created"));
            assertEquals(true,creation.createFromTurn(request).get("replayed"));
            assertEquals(1,goals.list("alice").size());
            verify(llm,times(1)).complete(anyString(),anyString());
        } finally {
            org.springframework.test.util.ReflectionTestUtils.setField(goals,"modelApprovalSecret","");
            org.springframework.test.util.ReflectionTestUtils.setField(goals,"modelApprovalSha","");
        }
    }

    @Test void conversationalStepRevisionPreservesHistoryBindsPatchAndUndoRestoresOriginal() throws Exception {
        String secret="test-only-goal-proof",sha="a".repeat(64),text="第二步你优化一下，来点详细的";
        org.springframework.test.util.ReflectionTestUtils.setField(goals,"modelApprovalSecret",secret);
        org.springframework.test.util.ReflectionTestUtils.setField(goals,"modelApprovalSha",sha);
        try {
            var created=goals.save("alice",null,new GoalRequest("学Java","","active",null,true,List.of(
                new GoalRequest.Action("first","固定时间","done",List.of("下午两小时")),
                new GoalRequest.Action("second","选学习资源","planned",List.of()))));
            Long id=(Long)created.get("id"),v=(Long)created.get("version");
            memory.rememberAgentTurn(new AgentTurnPersistRequest("revise-turn","alice",session.getId(),text,"收到","","","",Map.of()));
            String patch="[{\"actionId\":\"second\",\"title\":\"选教程的一节，运行一个最小示例\"}]";
            String digest=java.util.HexFormat.of().formatHex(java.security.MessageDigest.getInstance("SHA-256").digest(patch.getBytes(java.nio.charset.StandardCharsets.UTF_8)));
            String proof=goalProof(secret,sha,"revise-turn",text,"revise",id,"",v,digest);
            var request=new GoalManageRequest("alice","revise-turn",session.getId(),id,v,"revise",patch,proof);
            assertEquals(false,goals.manageFromTurn(new GoalManageRequest("alice","revise-turn",session.getId(),id,v,"revise","[]",proof)).get("applied"));
            var applied=goals.manageFromTurn(request);assertEquals(true,applied.get("applied"));
            var after=jsonActions(goals.get("alice",id));assertEquals("done",after.get(0).status());
            assertEquals(List.of("下午两小时"),after.get(0).feedback());assertEquals("second",after.get(1).id());
            assertEquals("选教程的一节，运行一个最小示例",after.get(1).title());assertEquals("planned",after.get(1).status());
            assertEquals(true,goals.manageFromTurn(request).get("replayed"));assertEquals(1,goals.checkIns("alice").size());
            var entry=(GoalCheckInResponse)applied.get("checkIn");goals.undoSync("alice",entry.id());
            assertEquals("选学习资源",jsonActions(goals.get("alice",id)).get(1).title());
            assertEquals(false,goals.manageFromTurn(request).get("applied"));
        } finally {
            org.springframework.test.util.ReflectionTestUtils.setField(goals,"modelApprovalSecret","");
            org.springframework.test.util.ReflectionTestUtils.setField(goals,"modelApprovalSha","");
        }
    }

    @Test void userDeclaredGoalClosureOverridesAutomaticCriteriaWithoutFakingStepsAndCanUndo() throws Exception {
        String secret="test-only-goal-proof",sha="a".repeat(64);
        org.springframework.test.util.ReflectionTestUtils.setField(goals,"modelApprovalSecret",secret);
        org.springframework.test.util.ReflectionTestUtils.setField(goals,"modelApprovalSha",sha);
        try {
            for(String op:List.of("complete","pause")) {
                var created=plannedGoal();Long id=(Long)created.get("id"),v=(Long)created.get("version");
                String text="complete".equals(op)?"整个目标我觉得已经达成了，标为完成吧":"这个目标我不想继续了，先停掉";
                memory.rememberAgentTurn(new AgentTurnPersistRequest(op+"-turn","alice",session.getId(),text,"收到","","","",Map.of()));
                String digest=java.util.HexFormat.of().formatHex(java.security.MessageDigest.getInstance("SHA-256").digest("[]".getBytes()));
                String proof=goalProof(secret,sha,op+"-turn",text,op,id,"",v,digest);
                var request=new GoalManageRequest("alice",op+"-turn",session.getId(),id,v,op,"[]",proof);
                assertThrows(org.springframework.web.server.ResponseStatusException.class,()->goals.manageFromTurn(
                    new GoalManageRequest("alice",op+"-turn",session.getId()+1,id,v,op,"[]",proof)));
                var result=goals.manageFromTurn(request);assertEquals(true,result.get("applied"));
                assertEquals("complete".equals(op)?"completed":"paused",goals.get("alice",id).get("status"));
                assertEquals("planned",jsonActions(goals.get("alice",id)).get(0).status());
                assertEquals(true,goals.manageFromTurn(request).get("replayed"));
                assertEquals(id,goals.conversationGoalId("alice",session.getId(),null));
                goals.undoSync("alice",((GoalCheckInResponse)result.get("checkIn")).id());
                assertEquals("active",goals.get("alice",id).get("status"));
            }
        } finally {
            org.springframework.test.util.ReflectionTestUtils.setField(goals,"modelApprovalSecret","");
            org.springframework.test.util.ReflectionTestUtils.setField(goals,"modelApprovalSha","");
        }
    }

    @Test void completionFeedbackCanAdaptExistingNextStepWithoutExplicitEdit() {
        String text="第一步做完了，我实际只改了名字并运行成功，感觉同时学两种类型太吃力";
        var created=goals.save("alice",null,new GoalRequest("学习Java","","active",null,true,List.of(
            new GoalRequest.Action("first","运行变量示例","planned",List.of()),
            new GoalRequest.Action("second","同时练习两种类型","planned",List.of("原来的学习限制"))),"连续一周保持学习"));
        Long id=(Long)created.get("id");
        var saved=goals.checkIn("alice",id,new GoalCheckInRequest("first","done",text,(Long)created.get("version"),true));
        var entry=(GoalCheckInResponse)saved.get("checkIn");
        when(llm.complete(anyString(),anyString())).thenReturn("""
            {"feedback":"你运行成功了，下一次先巩固一种类型。","completionCriteria":"连续一周保持学习","goalCompleted":false,"completionReason":"需要持续观察","completedEvidenceIds":["first"],"nextActionId":"second","nextStep":"","usedMessageIds":[],"stepAdjustments":[{"actionId":"second","title":"下次只改示例中的一个名字，运行并比较输出"}],"adjustmentReason":"用户表示同时练习两种类型太吃力"}
            """);
        var result=coach.reflect("alice",id,new GoalReflectRequest(entry.id(),(Long)((Map<?,?>)saved.get("goal")).get("version")));
        assertEquals(true,result.get("available"));var after=jsonActions(goals.get("alice",id));
        assertEquals("second",after.get(1).id());assertEquals("planned",after.get(1).status());
        assertEquals(List.of("原来的学习限制"),after.get(1).feedback());assertEquals("下次只改示例中的一个名字，运行并比较输出",after.get(1).title());
        // Manual entries retain their existing manual-only reflection path; conversation undo is tested separately.
        var reflect=(Map<?,?>)result.get("reflection");assertFalse(((List<?>)reflect.get("stepAdjustments")).isEmpty());
        verify(llm).complete(contains("不要求用户另说"),contains(text));
    }

    @Test void coachCannotRewriteDoneHistoryWhenAdaptingFutureSteps() {
        var created=plannedGoal();Long id=(Long)created.get("id");
        var saved=goals.checkIn("alice",id,new GoalCheckInRequest("walk","done","今天走完了，但很累",(Long)created.get("version"),true));
        var entry=(GoalCheckInResponse)saved.get("checkIn");
        when(llm.complete(anyString(),anyString())).thenReturn("""
            {"feedback":"先休息。","completionCriteria":"观察一周","goalCompleted":false,"completionReason":"需要持续观察","completedEvidenceIds":["walk"],"nextActionId":"","nextStep":"明天继续","usedMessageIds":[],"stepAdjustments":[{"actionId":"walk","title":"篡改已完成历史"}],"adjustmentReason":"不合适"}
            """);
        var result=coach.reflect("alice",id,new GoalReflectRequest(entry.id(),(Long)((Map<?,?>)saved.get("goal")).get("version")));
        assertEquals(false,result.get("available"));assertEquals("done",jsonActions(goals.get("alice",id)).get(0).status());
        assertNotEquals("篡改已完成历史",jsonActions(goals.get("alice",id)).get(0).title());
    }

    private List<GoalRequest.Action> jsonActions(Map<String,Object> goal) {
        return new ObjectMapper().convertValue(goal.get("actions"),new com.fasterxml.jackson.core.type.TypeReference<List<GoalRequest.Action>>(){});
    }

    private String goalProof(String secret,String sha,String requestId,String source,String operation,
            Long goalId,String actionId,Long version,String outcome) throws Exception {
        Map<String,Object> body=new LinkedHashMap<>();body.put("contract","psych-goal-gate-v1");body.put("modelSha",sha);
        body.put("owner","alice");body.put("sessionId",session.getId());body.put("requestId",requestId);
        body.put("operation",operation);body.put("expires",java.time.Instant.now().getEpochSecond()+300);
        body.put("sourceSha",java.util.HexFormat.of().formatHex(java.security.MessageDigest.getInstance("SHA-256").digest(source.getBytes(java.nio.charset.StandardCharsets.UTF_8))));
        if(goalId!=null){body.put("goalId",goalId);body.put("actionId",actionId);body.put("version",version);body.put("outcome",outcome);}
        String payload=Base64.getUrlEncoder().withoutPadding().encodeToString(new ObjectMapper().writeValueAsBytes(body));
        var mac=javax.crypto.Mac.getInstance("HmacSHA256");mac.init(new javax.crypto.spec.SecretKeySpec(secret.getBytes(java.nio.charset.StandardCharsets.UTF_8),"HmacSHA256"));
        return payload+"."+java.util.HexFormat.of().formatHex(mac.doFinal(payload.getBytes(java.nio.charset.StandardCharsets.US_ASCII)));
    }
    @Test void quotedOwnedGoalTitleDoesNotBecomeFutureIntent() {
        var created=goals.save("alice",null,new GoalRequest("准备工作汇报","","active",null,true,List.of(new GoalRequest.Action("write","写标题","planned",List.of()))));
        Long id=(Long)created.get("id");String text="我想聊聊目标「准备工作汇报」的进展：我刚才已经写标题了，这一步做完了。";
        memory.rememberAgentTurn(new AgentTurnPersistRequest("quoted-goal","alice",session.getId(),text,"收到","","","",Map.of()));
        var result=goals.syncFromTurn(new GoalSyncRequest("alice","quoted-goal",session.getId(),id,"write",(Long)created.get("version"),"done",text));
        assertEquals(true,result.get("applied"));
    }
    @Test void adjacentShortCompletionPersistsOnlyDiscussedStepAndReplaysAndUndoes() {
        String title="先确定一个最容易调整的饮食点，比如每天多吃一份蔬菜或减少一次含糖饮料。";
        var created=goals.save("alice",null,new GoalRequest("健康饮食","","active",null,true,List.of(
                new GoalRequest.Action("choose",title,"planned",List.of()),
                new GoalRequest.Action("meal","按这个调整点选择食物，并记下吃了什么。","planned",List.of()))));
        Long id=(Long)created.get("id"), version=(Long)created.get("version");
        memory.rememberAgentTurn(new AgentTurnPersistRequest("ask-step","alice",session.getId(),
                "第一个小步骤是什么","第一个小步骤是："+title.replace("或减少","，或者减少"),"","","",Map.of()));
        String text="OK，那我今天完成了";
        memory.beginAgentTurn(new AgentTurnBeginRequest("short-done","alice",session.getId(),text));
        var request=new GoalSyncRequest("alice","short-done",session.getId(),id,"choose",version,"done",text);
        var result=goals.syncFromTurn(request);assertEquals(true,result.get("applied"));
        var entry=(GoalCheckInResponse)result.get("checkIn");
        assertEquals("choose",entry.actionId());assertEquals(text,entry.note());
        var current=goals.list("alice").get(0);
        assertEquals("active",current.get("status"));
        assertEquals("done",((List<GoalRequest.Action>)current.get("actions")).get(0).status());
        assertEquals("planned",((List<GoalRequest.Action>)current.get("actions")).get(1).status());
        assertEquals(entry.id(),((GoalCheckInResponse)goals.syncFromTurn(request).get("checkIn")).id());
        assertEquals(1,goals.checkIns("alice").size());
        goals.undoSync("alice",entry.id());
        assertEquals(false,goals.syncFromTurn(request).get("applied"));
    }

    @Test void adjacentReferenceCannotCrossSessionsSelectAnotherStepOrDropOptout() {
        var created=plannedGoal();Long id=(Long)created.get("id"), version=(Long)created.get("version");
        memory.rememberAgentTurn(new AgentTurnPersistRequest("ask-step","alice",session.getId(),
                "第一个小步骤是什么","第一个小步骤是：散步十分钟","","","",Map.of()));
        var another=new ConversationSession();another.setUser(alice);another.setTitle("另一会话");
        another.setCreatedAt(LocalDateTime.now());another.setUpdatedAt(LocalDateTime.now());sessions.insert(another);
        String text="我今天完成了";
        memory.beginAgentTurn(new AgentTurnBeginRequest("different-session","alice",another.getId(),text));
        assertEquals(false,goals.syncFromTurn(new GoalSyncRequest("alice","different-session",another.getId(),
                id,"walk",version,"done",text)).get("applied"));
        memory.beginAgentTurn(new AgentTurnBeginRequest("optout-short","alice",session.getId(),text+"，不要同步"));
        assertEquals(false,goals.syncFromTurn(new GoalSyncRequest("alice","optout-short",session.getId(),
                id,"walk",version,"done",text)).get("applied"));
        assertTrue(goals.checkIns("alice").isEmpty());
    }

    @Test void duplicateOwnedStepReferencesAndInterveningTopicsDoNotAutoComplete() {
        var created=plannedGoal();Long id=(Long)created.get("id"),version=(Long)created.get("version");
        plannedGoal(); // Same title in a different goal is ambiguous.
        memory.rememberAgentTurn(new AgentTurnPersistRequest("ask-duplicate","alice",session.getId(),
                "第一个小步骤是什么","第一个小步骤是：散步十分钟","","","",Map.of()));
        String text="我今天完成了";
        memory.beginAgentTurn(new AgentTurnBeginRequest("ambiguous-short","alice",session.getId(),text));
        assertEquals(false,goals.syncFromTurn(new GoalSyncRequest("alice","ambiguous-short",session.getId(),
                id,"walk",version,"done",text)).get("applied"));
        memory.rememberAgentTurn(new AgentTurnPersistRequest("unrelated","alice",session.getId(),
                "换个话题","好的，散步十分钟是之前的步骤","","","",Map.of()));
        memory.beginAgentTurn(new AgentTurnBeginRequest("unrelated-short","alice",session.getId(),text));
        assertEquals(false,goals.syncFromTurn(new GoalSyncRequest("alice","unrelated-short",session.getId(),
                id,"walk",version,"done",text)).get("applied"));
        assertTrue(goals.checkIns("alice").isEmpty());
    }

    Map<String,Object> numberedGoal() {
        var created=goals.save("alice",null,new GoalRequest("机器学习","","active",null,true,List.of(
                new GoalRequest.Action("topic","确定今天学习的小主题","planned",List.of()),
                new GoalRequest.Action("study","学习主题并记录一句要点","planned",List.of()))));
        memory.rememberAgentTurn(new AgentTurnPersistRequest("number-source","alice",session.getId(),
                "设置一个机器学习的目标","已创建目标和两个步骤","","","",Map.of()));
        var source=receipts.findByUserAndRequestId(alice,"number-source").orElseThrow();
        source.setCreatedGoalId((Long)created.get("id"));receipts.save(source);
        return created;
    }

    @Test void numberedStepsBindCreatedGoalAndThenPreviousProgressWithReplayAndUndo() {
        var created=numberedGoal();Long id=(Long)created.get("id"),version=(Long)created.get("version");
        // Another goal with the same titles cannot steal this conversation's reference.
        goals.save("alice",null,new GoalRequest("另一个目标","","active",null,true,List.of(
                new GoalRequest.Action("other","确定今天学习的小主题","planned",List.of()))));
        assertEquals(id,goals.conversationGoalId("alice",session.getId(),"not-accepted-yet"));
        String first="OK，小步骤1我做完了";
        memory.beginAgentTurn(new AgentTurnBeginRequest("number-1","alice",session.getId(),first));
        assertEquals(id,goals.conversationGoalId("alice",session.getId(),"number-1"));
        assertEquals(false,goals.syncFromTurn(new GoalSyncRequest("alice","number-1",session.getId(),
                id,"study",version,"done",first)).get("applied"));
        var request=new GoalSyncRequest("alice","number-1",session.getId(),id,"topic",version,"done",first);
        var applied=goals.syncFromTurn(request);assertEquals(true,applied.get("applied"));
        var firstEntry=(GoalCheckInResponse)applied.get("checkIn");assertEquals(first,firstEntry.note());
        memory.rememberAgentTurn(new AgentTurnPersistRequest("number-1","alice",session.getId(),first,"已同步","","","",Map.of()));
        String second="第二步我完成了";
        memory.beginAgentTurn(new AgentTurnBeginRequest("number-2","alice",session.getId(),second));
        assertEquals(id,goals.conversationGoalId("alice",session.getId(),"number-2"));
        var current=goals.get("alice",id);
        var secondRequest=new GoalSyncRequest("alice","number-2",session.getId(),id,"study",(Long)current.get("version"),"done",second);
        var secondResult=goals.syncFromTurn(secondRequest);assertEquals(true,secondResult.get("applied"));
        assertEquals("active",goals.get("alice",id).get("status"));
        assertEquals(2,goals.checkIns("alice").size());
        assertEquals(firstEntry.id(),((GoalCheckInResponse)goals.syncFromTurn(request).get("checkIn")).id());
        goals.undoSync("alice",((GoalCheckInResponse)secondResult.get("checkIn")).id());
        assertEquals(false,goals.syncFromTurn(secondRequest).get("applied"));
    }

    @Test void numberedPartialStaysPlannedAndCannotCrossSessionOrActor() {
        var created=numberedGoal();Long id=(Long)created.get("id"),version=(Long)created.get("version");
        var other=new ConversationSession();other.setUser(alice);other.setTitle("无来源");
        other.setCreatedAt(LocalDateTime.now());other.setUpdatedAt(LocalDateTime.now());sessions.insert(other);
        String text="步骤1我做了一部分";
        memory.beginAgentTurn(new AgentTurnBeginRequest("other-number","alice",other.getId(),text));
        assertNull(goals.conversationGoalId("alice",other.getId(),"other-number"));
        assertEquals(false,goals.syncFromTurn(new GoalSyncRequest("alice","other-number",other.getId(),id,"topic",version,"partial",text)).get("applied"));
        memory.beginAgentTurn(new AgentTurnBeginRequest("partial-number","alice",session.getId(),text));
        assertEquals(true,goals.syncFromTurn(new GoalSyncRequest("alice","partial-number",session.getId(),id,"topic",version,"partial",text)).get("applied"));
        assertEquals("planned",((List<GoalRequest.Action>)goals.get("alice",id).get("actions")).get(0).status());
        User bob=new User();bob.setUsername("bob");bob.setPassword("test-only");bob.setRole("USER");users.insert(bob);
        assertNull(goals.conversationGoalId("bob",session.getId(),"number-source"));
    }

    @Test void interveningTopicAndEditedGoalInvalidateNumberReference() {
        var created=numberedGoal();Long id=(Long)created.get("id"),version=(Long)created.get("version");
        goals.save("alice",id,new GoalRequest("修改后目标","","active",version,true,List.of(
                new GoalRequest.Action("study","学习主题并记录一句要点","planned",List.of()),
                new GoalRequest.Action("topic","确定今天学习的小主题","planned",List.of()))));
        assertNull(goals.conversationGoalId("alice",session.getId(),null));
        String text="步骤1我做完了";
        memory.beginAgentTurn(new AgentTurnBeginRequest("edited-number","alice",session.getId(),text));
        assertEquals(false,goals.syncFromTurn(new GoalSyncRequest("alice","edited-number",session.getId(),id,"study",
                (Long)goals.get("alice",id).get("version"),"done",text)).get("applied"));
        memory.rememberAgentTurn(new AgentTurnPersistRequest("unrelated-number","alice",session.getId(),"换个话题","确定今天学习的小主题","","","",Map.of()));
        assertNull(goals.conversationGoalId("alice",session.getId(),null));
        assertTrue(goals.checkIns("alice").isEmpty());
    }

    @Test void futurePlansNeverBecomeCompletedProgress() {
        var created=plannedGoal();Long id=(Long)created.get("id");String text="我打算明天散步十分钟。";
        memory.rememberAgentTurn(new AgentTurnPersistRequest("future-turn","alice",session.getId(),text,"收到","","","",Map.of()));
        var result=goals.syncFromTurn(new GoalSyncRequest("alice","future-turn",session.getId(),id,"walk",(Long)created.get("version"),"done",text));
        assertEquals(false,result.get("applied"));assertTrue(goals.checkIns("alice").isEmpty());
    }

    @Test void wrongRecipientAndLocationCannotCreateCheckInsEvenWithValidOwnedReceipts() {
        String[][] cases = {
            {"给阿姨发一条近况消息", "我刚给老板发了一条近况消息。", "我刚给阿姨发了一条近况消息。"},
            {"把手机放到客厅充电十分钟", "我已经把手机放到卧室充电十分钟了。", "我已经把手机放到客厅充电十二分钟了。"}
        };
        for (int i=0;i<cases.length;i++) {
            var c=cases[i];
            var created=goals.save("alice",null,new GoalRequest("实体绑定测试","","active",null,true,
                    List.of(new GoalRequest.Action("bound-step",c[0],"planned",List.of()))));
            Long id=(Long)created.get("id"), version=(Long)created.get("version");
            String badId="wrong-binding-"+i;
            memory.rememberAgentTurn(new AgentTurnPersistRequest(badId,"alice",session.getId(),c[1],"收到","","","",Map.of()));
            int before=goals.checkIns("alice").size();
            var denied=goals.syncFromTurn(new GoalSyncRequest("alice",badId,session.getId(),id,"bound-step",version,"done",c[1]));
            assertEquals(false,denied.get("applied"));
            assertEquals("unverified_action_evidence",denied.get("reason"));
            assertEquals(before,goals.checkIns("alice").size());
            var unchanged=goals.list("alice").stream().filter(g->id.equals(g.get("id"))).findFirst().orElseThrow();
            assertEquals(version,unchanged.get("version"));
            assertTrue(unchanged.get("actions").toString().contains("status=planned"));
            String goodId="correct-binding-"+i;
            memory.rememberAgentTurn(new AgentTurnPersistRequest(goodId,"alice",session.getId(),c[2],"收到","","","",Map.of()));
            var applied=goals.syncFromTurn(new GoalSyncRequest("alice",goodId,session.getId(),id,"bound-step",version,"done",c[2]));
            assertEquals(true,applied.get("applied"));
            assertEquals(before+1,goals.checkIns("alice").size());
            var entry=(GoalCheckInResponse)applied.get("checkIn");
            assertEquals("done",entry.outcome());
            assertEquals(entry.id(),((GoalCheckInResponse)goals.syncFromTurn(new GoalSyncRequest("alice",goodId,session.getId(),id,"bound-step",version,"done",c[2])).get("checkIn")).id());
        }
        verifyNoInteractions(llm);
    }

}
