package com.vanilo.psych.agent.service;
import com.vanilo.psych.agent.entity.GoalCheckIn;
import com.vanilo.psych.agent.repository.*;
import org.springframework.http.HttpStatus;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;
import org.springframework.web.server.ResponseStatusException;
import java.time.*;
import java.time.temporal.TemporalAdjusters;
import java.util.*;
@Service
public class GoalWeeklyService {
    private final UserRepository users;private final GoalCheckInRepository records;
    public GoalWeeklyService(UserRepository users,GoalCheckInRepository records){this.users=users;this.records=records;}
    @Transactional(readOnly=true)
    public Map<String,Object> overview(String username,int offset) {
        int week=Math.min(12,Math.max(0,offset));var start=LocalDate.now().with(TemporalAdjusters.previousOrSame(DayOfWeek.MONDAY)).minusWeeks(week);
        var end=start.plusDays(7);var owner=users.findByUsername(username).orElseThrow(()->new ResponseStatusException(HttpStatus.NOT_FOUND));
        var raw=records.findTop1001ByUserAndCreatedAtGreaterThanEqualAndCreatedAtLessThanOrderByCreatedAtAsc(owner,start.atStartOfDay(),end.atStartOfDay());
        boolean limited=raw.size()>1000;var rows=raw.stream().limit(1000).filter(e->!e.isUndone()&&!e.getCreatedAt().isAfter(LocalDateTime.now())).toList();
        long done=count(rows,"done"),partial=count(rows,"partial"),blocked=count(rows,"blocked"),rest=count(rows,"rest");
        int completedSteps=(int)rows.stream().filter(e->e.getOutcome().equals("done")).map(e->e.getGoal().getId()+":"+e.getActionId()).distinct().count();
        long involved=rows.stream().map(e->e.getGoal().getId()).distinct().count();
        String summary=rows.isEmpty()?"这一周还没有留下进度记录。可以从一个愿意尝试的小步骤开始，也可以在聊天里说说现在的想法。":
                "这一周，你围绕 "+involved+" 个目标留下了 "+rows.size()+" 次记录，完成了 "+completedSteps+" 个小步骤。"+(partial>0?"还有 "+partial+" 次推进了一部分的尝试。":"")+(blocked>0?"你也记录了 "+blocked+" 次阻碍，这些线索可以用来缩小下一步。":"")+(rest>0?"另外留出了 "+rest+" 次休息。":"")+"这些记录描述的是实际行动，可以按现在合适的节奏继续。";
        List<Map<String,Object>> days=new ArrayList<>();for(int i=0;i<7;i++){var date=start.plusDays(i);var day=rows.stream().filter(e->e.getCreatedAt().toLocalDate().equals(date)).toList();days.add(Map.of("date",date.toString(),"done",count(day,"done"),"partial",count(day,"partial"),"blocked",count(day,"blocked"),"rest",count(day,"rest"),"future",date.isAfter(LocalDate.now())));}
        var grouped=new LinkedHashMap<Long,List<GoalCheckIn>>();rows.forEach(e->grouped.computeIfAbsent(e.getGoal().getId(),x->new ArrayList<>()).add(e));
        var perGoal=grouped.entrySet().stream().map(e->Map.<String,Object>of("id",e.getKey(),"title",e.getValue().get(0).getGoal().getTitle(),"done",count(e.getValue(),"done"),"partial",count(e.getValue(),"partial"),"blocked",count(e.getValue(),"blocked"),"rest",count(e.getValue(),"rest"))).toList();
        var newest=new ArrayList<>(rows);Collections.reverse(newest);
        var evidence=newest.stream().filter(e->!e.getNote().isBlank()).limit(12).map(e->Map.<String,Object>of("id",e.getId(),"goalId",e.getGoal().getId(),"goalTitle",e.getGoal().getTitle(),"actionTitle",e.getActionTitle(),"outcome",e.getOutcome(),"note",e.getNote(),"createdAt",e.getCreatedAt().toString())).toList();
        Map<String,Object> result=new LinkedHashMap<>();result.put("startDate",start.toString());result.put("endDate",end.minusDays(1).toString());result.put("weekOffset",week);result.put("limited",limited);result.put("recordCount",rows.size());result.put("completedSteps",completedSteps);result.put("doneRecords",done);result.put("partialRecords",partial);result.put("restRecords",rest);result.put("blockedRecords",blocked);result.put("goalCount",involved);result.put("summary",summary);result.put("days",days);result.put("goals",perGoal);result.put("evidence",evidence);return result;
    }
    private static long count(List<GoalCheckIn> rows,String outcome){return rows.stream().filter(e->e.getOutcome().equals(outcome)).count();}
}
