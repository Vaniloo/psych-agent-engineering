package com.vanilo.psych.agent.service;

import static org.junit.jupiter.api.Assertions.*;
import static org.mockito.Mockito.*;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.vanilo.psych.agent.dto.AgentTurnPersistRequest;
import com.vanilo.psych.agent.entity.*;
import com.vanilo.psych.agent.repository.*;

import org.junit.jupiter.api.*;
import org.springframework.web.server.ResponseStatusException;

import java.util.*;

class TurnPersistenceTests {
    AgentTurnReceiptMapper receipts = mock(AgentTurnReceiptMapper.class);
    UserMapper users = mock(UserMapper.class);
    ConversationMessageMapper messages = mock(ConversationMessageMapper.class);
    ConversationSessionMapper sessions = mock(ConversationSessionMapper.class);
    ConversationMemoryMapper memories = mock(ConversationMemoryMapper.class);
    UserProfileService profiles = mock(UserProfileService.class);
    ConversationMemoryService service =
            new ConversationMemoryService(
                    receipts,
                    new ObjectMapper(),
                    users,
                    messages,
                    sessions,
                    memories,
                    mock(UserProfileMapper.class),
                    profiles);
    User alice = new User();
    ConversationSession session = new ConversationSession();

    @BeforeEach
    void setup() {
        alice.setId(1L);
        session.setId(9L);
        when(users.lockByUsername("alice")).thenReturn(Optional.of(alice));
        when(sessions.findByIdAndUser(9L, alice)).thenReturn(Optional.of(session));
        when(memories.findByUser(alice)).thenReturn(Optional.of(new ConversationMemory()));
        when(profiles.getOrCreateProfile(alice)).thenReturn(new UserProfile());
    }

    AgentTurnPersistRequest request() {
        return new AgentTurnPersistRequest(
                "request-1", "alice", 9L, "今天想休息", "收到", "", "", "", Map.of());
    }

    @Test
    void duplicateTurnDoesNotWriteMessagesOrOutboxTwice() {
        when(receipts.save(any()))
                .thenAnswer(
                        call -> {
                            AgentTurnReceipt receipt = call.getArgument(0);
                            when(receipts.lockByUserAndRequestId(alice, "request-1"))
                                    .thenReturn(Optional.of(receipt));
                            return receipt;
                        });
        service.rememberAgentTurn(request());
        service.rememberAgentTurn(request());
        verify(messages, times(2)).insert(any());
        verify(receipts, times(1)).save(any());
    }

    @Test
    void requestIdCannotBeReusedForDifferentContent() {
        AgentTurnReceipt receipt = new AgentTurnReceipt();
        receipt.setFingerprint("different");
        receipt.setAssistantReply("已有回复");
        when(receipts.lockByUserAndRequestId(alice, "request-1")).thenReturn(Optional.of(receipt));
        assertEquals(
                409,
                assertThrows(
                                ResponseStatusException.class,
                                () -> service.rememberAgentTurn(request()))
                        .getStatusCode()
                        .value());
        verifyNoInteractions(messages);
    }

    @Test
    void sourceReceiptAndMemoryJobAreCreatedTogether() {
        service.rememberAgentTurn(request());
        verify(receipts)
                .save(
                        argThat(
                                item ->
                                        item.getMemoryStatus().equals("PENDING")
                                                && item.getUserMessage().equals("今天想休息")
                                                && item.getSessionId().equals(9L)
                                                && item.getCreatedAt() != null));
    }
}
