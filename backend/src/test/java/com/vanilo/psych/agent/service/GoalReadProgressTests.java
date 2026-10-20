package com.vanilo.psych.agent.service;

import static org.junit.jupiter.api.Assertions.*;
import static org.mockito.Mockito.*;

import com.vanilo.psych.agent.controller.InternalGoalController;
import com.vanilo.psych.agent.dto.GoalCheckInResponse;
import com.vanilo.psych.agent.security.InternalServiceAuthenticator;
import java.time.LocalDateTime;
import java.util.*;
import org.junit.jupiter.api.Test;

class GoalReadProgressTests {
    @Test void scopedReadIncludesSavedPartialRecordsButNotOtherGoalsOrUndoneWrites() {
        var goals=mock(GoalService.class);
        var controller=new InternalGoalController(goals,mock(InternalServiceAuthenticator.class),
                mock(GoalCreationService.class),mock(GoalCoachService.class));
        when(goals.named("alice","学习Java")).thenReturn(List.of(Map.of("id",7L,"title","学习Java")));
        when(goals.preferences("alice")).thenReturn(Map.of("autoSync",true));
        var now=LocalDateTime.now();
        List<GoalCheckInResponse> records=new ArrayList<>();
        records.add(new GoalCheckInResponse(1L,8L,"其他目标","a","其他步骤","partial","不可混入",now,"conversation",false));
        records.add(new GoalCheckInResponse(2L,7L,"学习Java","a","变量练习","partial","已撤销",now,"conversation",true));
        for(int i=0;i<12;i++)records.add(new GoalCheckInResponse(3L+i,7L,"学习Java","a","变量练习","partial","写了字符串变量",now,"conversation",false));
        when(goals.checkIns("alice")).thenReturn(records);
        var read=controller.list("internal","alice","学习Java",null,null,null);
        var progress=(List<?>)read.get("recentProgress");
        assertEquals(8,progress.size());
        assertTrue(progress.stream().map(GoalCheckInResponse.class::cast).allMatch(e->e.goalId()==7L&&!e.undone()&&e.outcome().equals("partial")));
        verify(goals).checkIns("alice");
        when(goals.named("alice","无匹配")).thenReturn(List.of());
        assertEquals(List.of(),controller.list("internal","alice","无匹配",null,null,null).get("recentProgress"));
    }
}
