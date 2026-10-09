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
@Import({ConversationMemoryService.class, GoalService.class, ObjectMapper.class})
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
}
