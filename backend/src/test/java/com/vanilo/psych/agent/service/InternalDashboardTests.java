package com.vanilo.psych.agent.service;

import static org.junit.jupiter.api.Assertions.*;
import static org.mockito.Mockito.*;

import com.vanilo.psych.agent.controller.InternalAgentController;
import com.vanilo.psych.agent.dto.ConversationOverviewResponse;
import com.vanilo.psych.agent.entity.User;
import com.vanilo.psych.agent.repository.UserMapper;
import com.vanilo.psych.agent.security.InternalServiceAuthenticator;

import org.junit.jupiter.api.Test;

import java.util.List;
import java.util.Optional;

class InternalDashboardTests {
    @Test
    void agentReportUsesOwnedCurrentOverviewEvenForAnAdminAccount() {
        var auth = mock(InternalServiceAuthenticator.class);
        var users = mock(UserMapper.class);
        var analysis = mock(PsychologicalService.class);
        var overview = mock(ConversationOverviewService.class);
        var user = new User();
        user.setId(7L);
        user.setUsername("alice");
        user.setRole("ADMIN");
        when(users.findByUsername("alice")).thenReturn(Optional.of(user));
        var today = java.time.LocalDate.now();
        var current =
                new ConversationOverviewResponse(
                        30,
                        today.minusDays(29),
                        today,
                        java.time.LocalDateTime.now(),
                        "empty",
                        "暂无原话",
                        List.of(),
                        new ConversationOverviewResponse.Coverage(0, 0, 0, false),
                        List.of(),
                        List.of(),
                        List.of(),
                        List.of(),
                        List.of(),
                        List.of());
        when(overview.overview("alice", 30, false)).thenReturn(current);
        when(analysis.getRecentReports(7L, 20)).thenReturn(List.of());
        var controller =
                new InternalAgentController(
                        auth,
                        mock(ConversationMemoryService.class),
                        mock(RiskDetectionService.class),
                        mock(CrisisWorkflowService.class),
                        mock(CrisisSupportService.class),
                        analysis,
                        users,
                        overview);
        var result = controller.dashboard("test-token", "alice", 20);
        assertSame(current, result.get("conversationOverview"));
        assertEquals(List.of(), result.get("reportSummaryResponse"));
        assertEquals(
                List.of("conversationOverview", "reportSummaryResponse"),
                List.copyOf(result.keySet()));
        verify(auth).verify("test-token");
        verify(analysis).getRecentReports(7L, 20);
        verifyNoMoreInteractions(analysis);
    }
}
