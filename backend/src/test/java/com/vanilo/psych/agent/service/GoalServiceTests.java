package com.vanilo.psych.agent.service;

import static org.junit.jupiter.api.Assertions.*;
import static org.mockito.Mockito.*;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.vanilo.psych.agent.dto.GoalRequest;
import com.vanilo.psych.agent.entity.*;
import com.vanilo.psych.agent.repository.*;

import org.junit.jupiter.api.*;
import org.springframework.web.server.ResponseStatusException;

import java.util.*;

class GoalServiceTests {
    SupportGoalRepository repository = mock(SupportGoalRepository.class);
    UserRepository users = mock(UserRepository.class);
    GoalService service = new GoalService(repository, users, new ObjectMapper());
    User alice = new User();

    @BeforeEach
    void setup() {
        alice.setId(1L);
        alice.setUsername("alice");
        when(users.findByUsername("alice")).thenReturn(Optional.of(alice));
    }

    GoalRequest request(Long version, boolean confirmed) {
        return new GoalRequest(
                "尝试休息",
                "",
                "active",
                version,
                confirmed,
                List.of(new GoalRequest.Action("action-1", "午后走一走", "done", List.of("今天尝试后舒服一点"))));
    }

    @Test
    void onlyExplicitUserConfirmedWritesAndActualFeedbackAreStored() {
        assertThrows(
                ResponseStatusException.class,
                () -> service.save("alice", null, request(null, false)));
        verify(repository, never()).saveAndFlush(any());
        when(repository.saveAndFlush(any()))
                .thenAnswer(
                        call -> {
                            SupportGoal goal = call.getArgument(0);
                            goal.setId(7L);
                            goal.setVersion(0L);
                            return goal;
                        });
        var result = service.save("alice", null, request(null, true));
        assertEquals("尝试休息", result.get("title"));
        assertTrue(result.get("actions").toString().contains("今天尝试后舒服一点"));
    }

    @Test
    void otherUsersGoalCannotBeUpdatedOrDeleted() {
        when(repository.findByIdAndUser(8L, alice)).thenReturn(Optional.empty());
        assertEquals(
                404,
                assertThrows(
                                ResponseStatusException.class,
                                () -> service.save("alice", 8L, request(0L, true)))
                        .getStatusCode()
                        .value());
        assertThrows(ResponseStatusException.class, () -> service.delete("alice", 8L));
        verify(repository, never()).saveAndFlush(any());
        verify(repository, never()).delete(any());
    }

    @Test
    void staleVersionsCannotOverwriteFeedback() {
        SupportGoal goal = new SupportGoal();
        goal.setId(8L);
        goal.setUser(alice);
        goal.setVersion(2L);
        when(repository.findByIdAndUser(8L, alice)).thenReturn(Optional.of(goal));
        assertEquals(
                409,
                assertThrows(
                                ResponseStatusException.class,
                                () -> service.save("alice", 8L, request(1L, true)))
                        .getStatusCode()
                        .value());
        verify(repository, never()).saveAndFlush(any());
    }

    @Test
    void duplicateActionIdentitiesAreRejected() {
        var action = new GoalRequest.Action("same", "行动", "planned", List.of());
        var request = new GoalRequest("目标", "", "active", null, true, List.of(action, action));
        assertThrows(ResponseStatusException.class, () -> service.save("alice", null, request));
    }
}
