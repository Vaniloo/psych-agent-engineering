package com.vanilo.psych.agent.service;

import com.fasterxml.jackson.core.type.TypeReference;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.vanilo.psych.agent.dto.GoalRequest;
import com.vanilo.psych.agent.dto.GoalCheckInRequest;
import com.vanilo.psych.agent.dto.GoalCheckInResponse;
import com.vanilo.psych.agent.dto.GoalSyncRequest;
import com.vanilo.psych.agent.entity.GoalCheckIn;
import com.vanilo.psych.agent.entity.SupportGoal;
import com.vanilo.psych.agent.entity.User;
import com.vanilo.psych.agent.repository.*;

import org.springframework.http.HttpStatus;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;
import org.springframework.web.server.ResponseStatusException;

import java.time.LocalDateTime;
import java.util.*;

@Service
@Transactional
public class GoalService {
    private final SupportGoalRepository goals;
    private final UserRepository users;
    private final ObjectMapper json;
    private final GoalCheckInRepository checkIns;
    private final AgentTurnReceiptRepository receipts;

    public GoalService(SupportGoalRepository goals, UserRepository users, ObjectMapper json, GoalCheckInRepository checkIns, AgentTurnReceiptRepository receipts) {
        this.goals = goals;
        this.users = users;
        this.json = json;
        this.checkIns = checkIns;
        this.receipts = receipts;
    }

    private User user(String username) {
        return users.findByUsername(username)
                .orElseThrow(() -> new ResponseStatusException(HttpStatus.NOT_FOUND));
    }

    private SupportGoal owned(String username, Long id) {
        return goals.findByIdAndUser(id, user(username))
                .orElseThrow(() -> new ResponseStatusException(HttpStatus.NOT_FOUND));
    }

    @Transactional(readOnly = true)
    public List<Map<String, Object>> list(String username) {
        return goals.findTop50ByUserOrderByUpdatedAtDesc(user(username)).stream()
                .map(this::view)
                .toList();
    }

    public Map<String, Object> save(String username, Long id, GoalRequest request) {
        if (!request.confirmed())
            throw new ResponseStatusException(HttpStatus.BAD_REQUEST, "目标需用户明确确认");
        if (request.actions().stream().map(GoalRequest.Action::id).distinct().count()
                != request.actions().size())
            throw new ResponseStatusException(HttpStatus.BAD_REQUEST, "行动标识重复");
        SupportGoal goal = id == null ? new SupportGoal() : owned(username, id);
        if (id == null) goal.setUser(user(username));
        else if (request.version() == null || !request.version().equals(goal.getVersion()))
            throw new ResponseStatusException(HttpStatus.CONFLICT, "目标已更新，请刷新后再保存");
        goal.setTitle(request.title().strip());
        goal.setDescription(Objects.toString(request.description(), ""));
        goal.setStatus(request.status());
        goal.setCompletionCriteria(Objects.toString(request.completionCriteria(),""));
        goal.setUpdatedAt(LocalDateTime.now());
        try {
            goal.setActionsJson(json.writeValueAsString(request.actions()));
        } catch (Exception e) {
            throw new IllegalArgumentException("行动格式错误", e);
        }
        return view(goals.saveAndFlush(goal));
    }

    @Transactional(readOnly=true)
    public Map<String,Object> get(String username,Long id) {return view(owned(username,id));}

    public void delete(String username, Long id) {
        SupportGoal goal = owned(username, id);
        checkIns.deleteByGoal(goal);
        goals.delete(goal);
    }

    @Transactional(readOnly=true)
    public List<GoalCheckInResponse> checkIns(String username) {
        return checkIns.findTop100ByUserOrderByCreatedAtDesc(user(username)).stream().map(this::checkInView).toList();
    }

    public Map<String,Object> checkIn(String username, Long goalId, GoalCheckInRequest request) {
        if (!request.confirmed()) throw new ResponseStatusException(HttpStatus.BAD_REQUEST, "请确认这次记录");
        SupportGoal goal=owned(username,goalId);
        if (!Objects.equals(request.version(),goal.getVersion()))
            throw new ResponseStatusException(HttpStatus.CONFLICT, "目标已更新，请刷新后再记录");
        if (!"active".equals(goal.getStatus()))
            throw new ResponseStatusException(HttpStatus.BAD_REQUEST, "请先将目标恢复为进行中");
        if (!Set.of("partial","done","blocked","rest").contains(request.outcome()))
            throw new ResponseStatusException(HttpStatus.BAD_REQUEST, "记录类型不支持");
        List<GoalRequest.Action> actions;
        try { actions=new ArrayList<>(json.readValue(goal.getActionsJson(),new TypeReference<List<GoalRequest.Action>>() {})); }
        catch (Exception e) { throw new IllegalStateException("行动记录读取失败",e); }
        int index=-1;
        for (int i=0;i<actions.size();i++) if (actions.get(i).id().equals(request.actionId())) {index=i;break;}
        if (index<0) throw new ResponseStatusException(HttpStatus.NOT_FOUND,"行动不存在");
        var action=actions.get(index);
        if (!"planned".equals(action.status())) throw new ResponseStatusException(HttpStatus.CONFLICT,"这一步已完成或已暂缓，请刷新或调整目标后继续");
        String note=Objects.toString(request.note(),"").strip();
        List<String> feedback=new ArrayList<>(action.feedback());
        if (!note.isBlank() && feedback.size()<20) feedback.add(note);
        actions.set(index,new GoalRequest.Action(action.id(),action.title(),
                "done".equals(request.outcome()) ? "done" : action.status(),feedback));
        LocalDateTime now=LocalDateTime.now();goal.setUpdatedAt(now);
        try { goal.setActionsJson(json.writeValueAsString(actions)); }
        catch (Exception e) { throw new IllegalStateException("行动记录保存失败",e); }
        // Saving the goal also checks its optimistic version; both writes share one transaction.
        goals.saveAndFlush(goal);
        GoalCheckIn entry=new GoalCheckIn();entry.setUser(goal.getUser());entry.setGoal(goal);
        entry.setActionId(action.id());entry.setActionTitle(action.title());entry.setOutcome(request.outcome());
        entry.setNote(note);entry.setCreatedAt(now);entry.setPreviousStatus(action.status());
        entry.setAppliedGoalVersion(goal.getVersion());entry.setFeedbackAdded(!note.isBlank() && action.feedback().size()<20);
        checkIns.saveAndFlush(entry);
        return Map.of("goal",view(goal),"checkIn",checkInView(entry));
    }
    public Map<String,Object> preferences(String username) {return Map.of("autoSync",user(username).isGoalAutoSync());}
    public Map<String,Object> setAutoSync(String username,boolean enabled) {
        User owner=user(username);owner.setGoalAutoSync(enabled);users.saveAndFlush(owner);return Map.of("autoSync",enabled);
    }
    public Map<String,Object> syncFromTurn(GoalSyncRequest request) {
        User owner=user(request.username());
        if (!owner.isGoalAutoSync()) return Map.of("applied",false,"reason","disabled");
        var receipt=receipts.findByUserAndRequestId(owner,request.requestId())
                .orElseThrow(()->new ResponseStatusException(HttpStatus.NOT_FOUND,"原始对话尚未保存"));
        if (!Objects.equals(receipt.getSessionId(),request.sessionId()) || !receipt.getUserMessage().contains(request.quote()))
            throw new ResponseStatusException(HttpStatus.BAD_REQUEST,"记录依据必须来自本轮用户原话");
        var currentGoal=goals.findByIdAndUser(request.goalId(),owner)
                .orElseThrow(()->new ResponseStatusException(HttpStatus.NOT_FOUND));
        // A quoted owned goal title is context, not a new future intention.
        String intentEvidence=request.quote().replace("「"+currentGoal.getTitle()+"」", "（目标名称）")
                .replace("“"+currentGoal.getTitle()+"”", "（目标名称）");
        if ("partial".equals(request.outcome())) {
            // An unfinished step can still contain real progress. Keep all other intent guards.
            intentEvidence=intentEvidence.replaceAll("还没(?:完全)?(?:完成|做完|写完|读完)|没完成", "");
            if (!java.util.regex.Pattern.compile("已经|刚刚|刚才|今天.*了|昨天.*了|做了一点|试了一点").matcher(request.quote()).find())
                return Map.of("applied",false,"reason","ambiguous_expression");
        }
        if (Set.of("partial","done").contains(request.outcome()) &&
                java.util.regex.Pattern.compile("打算|准备|计划|如果|假如|明天|还没|没有|不想|没做|没完成|希望|想要|想去|建议|他说|她说").matcher(intentEvidence).find())
            return Map.of("applied",false,"reason","ambiguous_expression");
        var previous=checkIns.findByUserAndSourceRequestIdAndActionId(owner,request.requestId(),request.actionId());
        if (previous.isPresent()) {
            var entry=previous.get();
            if (!Objects.equals(entry.getGoal().getId(),request.goalId())) throw new ResponseStatusException(HttpStatus.CONFLICT);
            return Map.of("applied",!entry.isUndone(),"checkIn",checkInView(entry));
        }
        var result=checkIn(request.username(),request.goalId(),new GoalCheckInRequest(request.actionId(),request.outcome(),request.quote(),request.version(),true));
        var saved=(GoalCheckInResponse)result.get("checkIn");var entry=checkIns.findById(saved.id()).orElseThrow();
        entry.setSource("conversation");entry.setSourceRequestId(request.requestId());entry.setSessionId(request.sessionId());checkIns.saveAndFlush(entry);
        return Map.of("applied",true,"checkIn",checkInView(entry));
    }
    public Map<String,Object> undoSync(String username,Long checkInId) {
        var entry=checkIns.findByIdAndUser(checkInId,user(username))
                .orElseThrow(()->new ResponseStatusException(HttpStatus.NOT_FOUND));
        if (!"conversation".equals(entry.getSource())) throw new ResponseStatusException(HttpStatus.BAD_REQUEST,"只撤销对话自动同步记录");
        var goal=entry.getGoal();
        if (entry.isUndone()) return Map.of("goal",view(goal),"checkIn",checkInView(entry));
        if (!Objects.equals(goal.getVersion(),entry.getAppliedGoalVersion()))
            throw new ResponseStatusException(HttpStatus.CONFLICT,"目标已有后续调整，请在目标页面核对后修改");
        try {
            var actions=new ArrayList<>(json.readValue(goal.getActionsJson(),new TypeReference<List<GoalRequest.Action>>() {}));
            int index=-1;for(int i=0;i<actions.size();i++)if(actions.get(i).id().equals(entry.getActionId())){index=i;break;}
            if(index<0)throw new ResponseStatusException(HttpStatus.CONFLICT,"行动已有变化，请在目标页面核对");
            var action=actions.get(index);var feedback=new ArrayList<>(action.feedback());
            if(entry.isFeedbackAdded()&&!feedback.isEmpty()&&feedback.get(feedback.size()-1).equals(entry.getNote()))feedback.remove(feedback.size()-1);
            actions.set(index,new GoalRequest.Action(action.id(),action.title(),entry.getPreviousStatus(),feedback));
            goal.setActionsJson(json.writeValueAsString(actions));goal.setUpdatedAt(LocalDateTime.now());goals.saveAndFlush(goal);
            entry.setUndone(true);checkIns.saveAndFlush(entry);
            return Map.of("goal",view(goal),"checkIn",checkInView(entry));
        } catch(ResponseStatusException e){throw e;}catch(Exception e){throw new IllegalStateException("进度撤销失败",e);}
    }
    private GoalCheckInResponse checkInView(GoalCheckIn entry) {
        return new GoalCheckInResponse(entry.getId(),entry.getGoal().getId(),entry.getGoal().getTitle(),
                entry.getActionId(),entry.getActionTitle(),entry.getOutcome(),entry.getNote(),entry.getCreatedAt(),entry.getSource(),entry.isUndone());
    }

    private Map<String, Object> view(SupportGoal goal) {
        try {
            return Map.of(
                    "id",
                    goal.getId(),
                    "version",
                    goal.getVersion(),
                    "title",
                    goal.getTitle(),
                    "description",
                    goal.getDescription(),
                    "completionCriteria",Objects.toString(goal.getCompletionCriteria(),""),
                    "status",
                    goal.getStatus(),
                    "updatedAt",
                    goal.getUpdatedAt().toString(),
                    "actions",
                    json.readValue(
                            goal.getActionsJson(),
                            new TypeReference<List<GoalRequest.Action>>() {}));
        } catch (Exception e) {
            throw new IllegalStateException("目标记录读取失败", e);
        }
    }
}
