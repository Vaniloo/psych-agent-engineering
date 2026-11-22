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
@Import({ConversationMemoryService.class, AgentMetricsService.class, ObjectMapper.class})
class AgentPersistenceIntegrationTests {
    @MockitoBean LlmService llm;
    @MockitoBean RoleCardService roles;
    @MockitoBean UserProfileService profiles;
    @MockitoBean StringRedisTemplate redis;
    @Autowired JdbcTemplate jdbc;
    @Autowired KnowledgeDocumentMapper documents;
    @Autowired UserModelApiMapper settings;
    @Autowired RiskAlertEventMapper alerts;
    @Autowired UserMapper users;
    @Autowired ConversationSessionMapper sessions;
    @Autowired ConversationMessageMapper messages;
    @Autowired UserProfileMapper profileRecords;
    @Autowired AgentTurnReceiptMapper receipts;
    @Autowired ConversationMemoryService memory;
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
        memory.rememberAgentTurn(new AgentTurnPersistRequest("saved-receipt", "alice", session.getId(),
                "我喜欢音乐", "收到", "", "", "", Map.of()));
        assertDoesNotThrow(() -> memory.requireSavedTurn("alice", "saved-receipt"));
        var other = new User(); other.setUsername("other-status-owner"); other.setPassword("test"); other.setRole("USER");
        users.insert(other);
        assertThrows(org.springframework.web.server.ResponseStatusException.class,
                () -> memory.requireSavedTurn("other-status-owner", "saved-receipt"));
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
        run.setNodeDurationsMs(Map.of("agent_tool",12.0,"agent_observe",3.0));
        run.setPipeline(Map.of("router",Map.of("provider","rules","supportLevel","self_help"),"executor",Map.of("contract","psych-executor-v2"),"memory",Map.of("delivery","queued")));
        run.setCapabilityCalls(List.of(Map.of("name","memory_search","durationMs",12.0,"transport","local","status","ok")));
        metrics.recordRun("alice",session.getId(),run);metrics.recordRun("alice",session.getId(),run);
        var summary=metrics.summary("alice",7);assertEquals(1,summary.get("totalRuns"));assertEquals(1L,summary.get("pipelineSamples"));assertEquals(0,summary.get("ttftSamples"));
        assertEquals(1,((List<?>)summary.get("capabilities")).size());
        assertEquals(run.getPipeline(),metrics.recent("alice",30,7).get(0).get("pipeline"));
        assertTrue(metrics.recent("other",30,7).isEmpty());
        var old=new AgentRunMetricsResponse();old.setRequestId("metric-old");old.setStartedAt(java.time.OffsetDateTime.now().toString());metrics.recordRun("alice",session.getId(),old);
        assertEquals(2,metrics.summary("alice",7).get("totalRuns"));assertEquals(1L,metrics.summary("alice",7).get("pipelineSamples"));
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


}
