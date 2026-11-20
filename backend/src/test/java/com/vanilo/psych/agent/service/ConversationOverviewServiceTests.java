package com.vanilo.psych.agent.service;

import static org.junit.jupiter.api.Assertions.*;
import static org.mockito.Mockito.*;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.vanilo.psych.agent.entity.*;
import com.vanilo.psych.agent.repository.*;
import org.junit.jupiter.api.*;
import org.mockito.ArgumentCaptor;
import org.springframework.web.server.ResponseStatusException;
import java.time.*;
import java.util.*;

class ConversationOverviewServiceTests {
    UserMapper users = mock(UserMapper.class);
    ConversationSessionMapper sessions = mock(ConversationSessionMapper.class);
    ConversationMessageMapper messages = mock(ConversationMessageMapper.class);
    LlmService llm = mock(LlmService.class);
    Clock clock = Clock.fixed(Instant.parse("2026-10-09T12:00:00Z"), ZoneOffset.UTC);
    ConversationOverviewService service = new ConversationOverviewService(users, sessions, messages, llm,
            new ObjectMapper().findAndRegisterModules(), clock);
    User alice = user(1L,"alice"), bob = user(2L,"bob");
    ConversationSession first = session(7L,alice), second = session(8L,alice), foreign = session(9L,bob);
    static final String VALID = """
            {"summary":"在这几次对话中，你主要谈到了工作进度带来的压力，也提到了休息不足时的疲惫。较晚的一次表达里，你说出去走了走以后感觉轻松了一些，这是一段具体的应对体验。", "summaryMessageIds":[11,21],
            "themes":[{"title":"工作与休息","description":"工作压力和休息不足都在这段时间的表达中出现，需要结合各自发生的情境理解。","messageIds":[11,21]}],
            "emotionObservations":[{"messageId":11,"category":"tense","quote":"压力"},{"messageId":12,"category":"calm","quote":"轻松了一点"}],"reflectionQuestions":["哪些时刻让你感觉轻松一些？"]}
            """;
    @BeforeEach void setup() {
        when(users.findByUsername("alice")).thenReturn(Optional.of(alice));
        when(sessions.recentSince(eq(alice),any())).thenReturn(List.of(first,second));
        when(messages.recentEvidence(eq(alice),eq(first),eq("user"),any()))
                .thenReturn(List.of(message(12L,alice,first,"user","昨天出去走了走，感觉轻松了一点。"),message(11L,alice,first,"user","工作进度让我有些压力。")));
        when(messages.recentEvidence(eq(alice),eq(second),eq("user"),any()))
                .thenReturn(List.of(message(21L,alice,second,"user","最近休息不够，有些疲惫。")));
        when(llm.complete(anyString(),anyString())).thenReturn(VALID);
    }
    @Test void aggregatesMultipleOwnedConversationsAndUsesEvidenceReferences() {
        var result = service.overview("alice",30,false);
        assertEquals("ready",result.status()); assertEquals(2,result.coverage().conversationCount());
        assertEquals(3,result.coverage().expressionCount()); assertEquals(List.of(11L,21L),result.summaryMessageIds());
        assertEquals(2,result.emotionObservations().size());
        assertEquals(2,result.sources().size()); assertFalse(result.summary().contains("低风险"));
        var prompt=ArgumentCaptor.forClass(String.class);verify(llm).complete(anyString(),prompt.capture());
        assertTrue(prompt.getValue().contains("工作进度")); assertTrue(prompt.getValue().contains("休息不够"));
    }
    @Test void emptyOrVerySmallSamplesNeverInventAnAssessment() {
        when(sessions.recentSince(eq(alice),any())).thenReturn(List.of());
        assertEquals("empty",service.overview("alice",7,false).status()); verifyNoInteractions(llm);
        when(sessions.recentSince(eq(alice),any())).thenReturn(List.of(second));
        assertEquals("limited",service.overview("alice",7,false).status()); verifyNoInteractions(llm);
    }
    @Test void otherOwnersAssistantRepliesAndOutOfWindowMessagesAreExcluded() {
        var old=message(40L,alice,first,"user","窗口之外");old.setCreatedAt(LocalDateTime.of(2026,1,1,0,0));
        when(sessions.recentSince(eq(alice),any())).thenReturn(List.of(first,foreign));
        when(messages.recentEvidence(eq(alice),eq(first),eq("user"),any()))
                .thenReturn(List.of(message(11L,alice,first,"user","我的表达"),message(31L,bob,first,"user","另一用户私密表达"),message(32L,alice,first,"assistant","助手建议不等于用户经历"),old));
        var result=service.overview("alice",30,false);
        assertEquals(1,result.evidence().size()); assertEquals("我的表达",result.evidence().get(0).text());
        verify(messages,never()).recentEvidence(eq(alice),eq(foreign),anyString(),any());
        verifyNoInteractions(llm);
    }
    @Test void unknownOrSingleConversationSummaryCitationsAreRejected() {
        when(llm.complete(anyString(),anyString())).thenReturn(VALID.replace("\"summaryMessageIds\":[11,21]","\"summaryMessageIds\":[11,999]"));
        var result=service.overview("alice",30,false); assertEquals("fallback",result.status());
        assertEquals(3,result.evidence().size()); assertTrue(result.themes().isEmpty()); assertTrue(result.summaryMessageIds().isEmpty());
        when(llm.complete(anyString(),anyString())).thenReturn(VALID.replace("\"summaryMessageIds\":[11,21]","\"summaryMessageIds\":[11,12]"));
        assertEquals("fallback",service.overview("alice",30,true).status());
    }
    @Test void unavailableModelKeepsRawRecordsAndDoesNotClaimHealthOrStability() {
        when(llm.complete(anyString(),anyString())).thenThrow(new IllegalStateException("offline"));
        var result=service.overview("alice",30,false);assertEquals("fallback",result.status());
        assertEquals(3,result.evidence().size());assertFalse(result.summary().contains("稳定"));assertFalse(result.summary().contains("低风险"));
    }
    @Test void cacheIsWindowAndSourceSpecificAndExplicitRefreshRecomputes() {
        service.overview("alice",30,false);service.overview("alice",30,false);verify(llm,times(1)).complete(anyString(),anyString());
        service.overview("alice",30,true);verify(llm,times(2)).complete(anyString(),anyString());
        service.overview("alice",7,false);verify(llm,times(3)).complete(anyString(),anyString());
        when(messages.recentEvidence(eq(alice),eq(second),eq("user"),any()))
                .thenReturn(List.of(message(21L,alice,second,"user","这条原话已变化。")));
        service.overview("alice",30,false);verify(llm,times(4)).complete(anyString(),anyString());
    }
    @Test void emotionChartsRejectUnknownReferencesQuotesAndCategoriesAndDeduplicate() {
        when(llm.complete(anyString(),anyString())).thenReturn(VALID.replace(
            "\"emotionObservations\":[{\"messageId\":11,\"category\":\"tense\",\"quote\":\"压力\"},{\"messageId\":12,\"category\":\"calm\",\"quote\":\"轻松了一点\"}]",
            "\"emotionObservations\":[{\"messageId\":999,\"category\":\"sad\",\"quote\":\"压力\"},{\"messageId\":11,\"category\":\"tense\",\"quote\":\"压力\"},{\"messageId\":11,\"category\":\"tense\",\"quote\":\"压力\"},{\"messageId\":12,\"category\":\"score_90\",\"quote\":\"轻松\"},{\"messageId\":12,\"category\":\"calm\",\"quote\":\"编造的原话\"}]"));
        var result=service.overview("alice",30,false);
        assertEquals("ready",result.status()); assertEquals(1,result.emotionObservations().size());
        assertEquals(11L,result.emotionObservations().get(0).messageId());
        assertEquals("压力",result.emotionObservations().get(0).quote());
    }
    @Test void missingUserCannotReadAnyConversationAndWindowsAreBounded() {
        assertThrows(ResponseStatusException.class,()->service.overview("missing",30,false));
        verifyNoInteractions(messages);
        var result=service.overview("alice",999,false);assertEquals(90,result.days());
        assertEquals(LocalDate.of(2026,7,12),result.periodStart());
    }
    static User user(Long id,String name){var u=new User();u.setId(id);u.setUsername(name);return u;}
    static ConversationSession session(Long id,User owner){var s=new ConversationSession();s.setId(id);s.setUser(owner);s.setTitle("对话 "+id);return s;}
    static ConversationMessage message(Long id,User owner,ConversationSession session,String role,String content){
        var m=new ConversationMessage();m.setId(id);m.setUser(owner);m.setSession(session);m.setRole(role);m.setContent(content);m.setCreatedAt(LocalDateTime.of(2026,10,8,12,0));return m;
    }
}
