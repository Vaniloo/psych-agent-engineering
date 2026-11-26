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
    @MockitoBean UserProfileService profiles;
    @MockitoBean StringRedisTemplate redis;
    @Autowired JdbcTemplate jdbc;
    @Autowired KnowledgeDocumentMapper documents;
    @Autowired RiskAlertEventMapper alerts;
    @Autowired UserMapper users;
    @Autowired ConversationSessionMapper sessions;
    @Autowired ConversationMessageMapper messages;
    @Autowired UserProfileMapper profileRecords;
    @Autowired AgentTurnReceiptMapper receipts;
    @Autowired ConversationMemoryService memory;
    @Autowired AgentMetricsService metrics;
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
    }

    @Test
    void replacingKnowledgeKeepsItsOriginalKey() {
        var document = new KnowledgeDocument("test-doc", "初稿", "demo", "integration", LocalDateTime.now());
        documents.upsert(document);
        document.setContent("修订稿");
        documents.upsert(document);
        assertEquals(List.of("修订稿"), documents.findBySource("integration").stream()
                .map(KnowledgeDocument::getContent).toList());
        assertTrue(documents.page(org.springframework.data.domain.PageRequest.of(0, 10)).stream()
                .anyMatch(row -> row.getId().equals("test-doc")));

    }

    @Test
    void evaluationMetricsPreserveLegacyColumnNamesAndCrisisEventsStayOwned() {
        var evaluation = new ObjectMapper().createObjectNode()
                .put("datasetVersion", "integration").put("nDcgAt5", 0.75).put("p95LatencyMs", 110);
        metrics.recordEvaluation(evaluation);
        assertEquals(0.75, metrics.latestEvaluation().get("nDcgAt5"));
        assertEquals(110.0, metrics.latestEvaluation().get("p95LatencyMs"));

        var event = new RiskAlertEvent(null, "alice", "test", "high", "RECORDED", LocalDateTime.now());
        alerts.insert(event);
        assertEquals(event.getId(), alerts.findByUsername("alice").get(0).getId());
        assertTrue(alerts.findByUsername("bob").isEmpty());
    }

    @Test
    void completingAMemoryJobAppliesTheRuntimeSummaryOnlyOnce() {
        memory.rememberAgentTurn(new AgentTurnPersistRequest("summary-turn", "alice", session.getId(),
                "最近考试压力大，散步能让我放松", "收到", "", "", "", Map.of()));
        Long job = receipts.memoryJobs("PENDING", 20).get(0).getId();
        var update = new MemoryUpdateRequest("在聊考试压力", "近期备考紧张", "散步能帮助放松",
                Map.of("copingStrategies", "散步"));
        assertTrue(memory.completeMemoryJob(job, update));
        assertTrue(receipts.memoryJobs("PENDING", 20).isEmpty());
        var context = memory.getAgentContext("alice", session.getId(), 10);
        assertEquals("在聊考试压力", context.getSessionSummary());
        assertEquals("散步能帮助放松", context.getLongTermMemory());
        assertEquals("散步", profileRecords.findByUser(alice).orElseThrow().getCopingStrategies());

        var replay = new MemoryUpdateRequest("不应覆盖", "", "", Map.of());
        assertFalse(memory.completeMemoryJob(job, replay));
        assertEquals("在聊考试压力", memory.getAgentContext("alice", session.getId(), 10).getSessionSummary());
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
