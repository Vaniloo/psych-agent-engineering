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
    @org.springframework.beans.factory.annotation.Value("${psych.agent-runtime.internal-service-token:}")
    private String modelApprovalSecret="";
    @org.springframework.beans.factory.annotation.Value("${GOAL_GATE_EXPECTED_SHA:}")
    private String modelApprovalSha="";
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

    @Transactional(readOnly=true)
    public List<Map<String,Object>> listByStatus(String username,String status) {
        if(!List.of("active","completed","paused").contains(status))
            throw new ResponseStatusException(HttpStatus.BAD_REQUEST,"无效目标状态");
        return goals.findTop50ByUserAndStatusOrderByUpdatedAtDesc(user(username),status).stream().map(this::view).toList();
    }

    @Transactional(readOnly = true)
    public List<Map<String, Object>> named(String username, String title) {
        if(title==null||title.isBlank()||title.length()>200)
            throw new ResponseStatusException(HttpStatus.BAD_REQUEST,"目标名称无效");
        return goals.findTop10ByUserAndTitleOrderByUpdatedAtDesc(user(username),title.strip()).stream()
                .map(this::view).toList();
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
        var actions = readActions(goal);
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
        List<GoalRequest.Action> ownedActions;
        try { ownedActions=json.readValue(currentGoal.getActionsJson(),new TypeReference<List<GoalRequest.Action>>() {}); }
        catch (Exception e) { throw new IllegalStateException("行动记录读取失败",e); }
        var selected=ownedActions.stream().filter(a->a.id().equals(request.actionId())).findFirst()
                .orElseThrow(()->new ResponseStatusException(HttpStatus.NOT_FOUND,"行动不存在"));
        var previous=checkIns.findByUserAndSourceRequestIdAndActionId(owner,request.requestId(),request.actionId());
        if (previous.isPresent()) {
            var entry=previous.get();
            if (!Objects.equals(entry.getGoal().getId(),request.goalId())
                    || !Objects.equals(entry.getOutcome(),request.outcome())
                    || !Objects.equals(entry.getNote(),request.quote().strip()))
                throw new ResponseStatusException(HttpStatus.CONFLICT);
            return Map.of("applied",!entry.isUndone(),"checkIn",checkInView(entry));
        }
        boolean modelApproved=request.modelApproval()!=null;
        if(modelApproved&&(!receipt.getUserMessage().equals(request.quote())
                ||!verifyModelApproval(request.modelApproval(),request.username(),request.sessionId(),request.requestId(),
                    receipt.getUserMessage(),"progress",request.goalId(),request.actionId(),request.version(),request.outcome())))
            return Map.of("applied",false,"reason","invalid_model_approval");
        boolean contextual = !modelApproved&&supportsAdjacentStep(owner, receipt, request);
        if (!modelApproved && !contextual && !GoalEvidencePolicy.supports(request.quote(),selected.title(),request.outcome()))
            return Map.of("applied",false,"reason","unverified_action_evidence");
        // A quoted owned goal title is context, not a new future intention.
        String intentEvidence=request.quote().replace("「"+currentGoal.getTitle()+"」", "（目标名称）")
                .replace("“"+currentGoal.getTitle()+"”", "（目标名称）");
        if (!modelApproved && "partial".equals(request.outcome())) {
            // An unfinished step can still contain real progress. Keep all other intent guards.
            intentEvidence=intentEvidence.replaceAll("还没(?:完全)?(?:完成|做完|写完|读完)|没完成", "");
            if (!contextual && !java.util.regex.Pattern.compile("已经|刚刚|刚才|今天.*了|昨天.*了|做了一点|试了一点").matcher(request.quote()).find())
                return Map.of("applied",false,"reason","ambiguous_expression");
        }
        if (!modelApproved && Set.of("partial","done").contains(request.outcome()) &&
                java.util.regex.Pattern.compile("打算|准备|计划|如果|假如|明天|还没|没有|不想|没做|没完成|希望|想要|想去|建议|他说|她说").matcher(intentEvidence).find())
            return Map.of("applied",false,"reason","ambiguous_expression");
        var result=checkIn(request.username(),request.goalId(),new GoalCheckInRequest(request.actionId(),request.outcome(),request.quote(),request.version(),true));
        var saved=(GoalCheckInResponse)result.get("checkIn");var entry=checkIns.findById(saved.id()).orElseThrow();
        entry.setSource("conversation");entry.setSourceRequestId(request.requestId());entry.setSessionId(request.sessionId());checkIns.saveAndFlush(entry);
        return Map.of("applied",true,"checkIn",checkInView(entry));
    }

    public Map<String,Object> manageFromTurn(com.vanilo.psych.agent.dto.GoalManageRequest request) {
        var owner=user(request.username());
        if(!owner.isGoalAutoSync())return Map.of("applied",false,"reason","disabled");
        var receipt=receipts.findOwnedForGoalCreation(owner,request.requestId())
                .orElseThrow(()->new ResponseStatusException(HttpStatus.NOT_FOUND,"原始对话尚未保存"));
        if(!Objects.equals(receipt.getSessionId(),request.sessionId()))throw new ResponseStatusException(HttpStatus.BAD_REQUEST);
        var goal=owned(request.username(),request.goalId());
        String digest;
        try {digest=java.util.HexFormat.of().formatHex(java.security.MessageDigest.getInstance("SHA-256")
                .digest(request.editsJson().getBytes(java.nio.charset.StandardCharsets.UTF_8)));}
        catch(Exception e){throw new IllegalStateException(e);}
        if(!verifyModelApproval(request.modelApproval(),request.username(),request.sessionId(),request.requestId(),
                receipt.getUserMessage(),request.operation(),request.goalId(),"",request.version(),digest))
            return Map.of("applied",false,"reason","invalid_model_approval");
        String outcome=switch(request.operation()){case "revise"->"revised";case "complete"->"goal_completed";case "pause"->"paused";default->throw new ResponseStatusException(HttpStatus.BAD_REQUEST);};
        var previous=checkIns.findByUserAndSourceRequestId(owner,request.requestId());
        if(!previous.isEmpty()) {
            var entry=previous.get(0);
            if(previous.size()!=1||!entry.getGoal().getId().equals(goal.getId())||!entry.getOutcome().equals(outcome))
                throw new ResponseStatusException(HttpStatus.CONFLICT);
            try {if(!digest.equals(json.readTree(entry.getCoachingJson()).path("payloadSha").asText()))throw new ResponseStatusException(HttpStatus.CONFLICT);}
            catch(ResponseStatusException e){throw e;}catch(Exception e){throw new IllegalStateException(e);}
            return Map.of("applied",!entry.isUndone(),"goal",view(goal),"checkIn",checkInView(entry),"operation",request.operation(),"replayed",true);
        }
        if(!goal.getVersion().equals(request.version())||!"active".equals(goal.getStatus()))
            throw new ResponseStatusException(HttpStatus.CONFLICT,"目标已有调整，请刷新后查看");
        try {
            var actions=readActions(goal);
            var edits=json.readTree(request.editsJson());
            if(!edits.isArray()||edits.size()>12||("revise".equals(request.operation())?edits.isEmpty():!edits.isEmpty()))
                throw new ResponseStatusException(HttpStatus.BAD_REQUEST);
            var snapshot=Map.of("actionsJson",goal.getActionsJson(),"status",goal.getStatus(),"completionCriteria",Objects.toString(goal.getCompletionCriteria(),""));
            Set<String> changed=new HashSet<>();
            for(var edit:edits) {
                if(!edit.path("actionId").isTextual()||!edit.path("title").isTextual())throw new ResponseStatusException(HttpStatus.BAD_REQUEST);
                String id=edit.path("actionId").asText(),title=edit.path("title").asText().strip();
                int index=-1;for(int i=0;i<actions.size();i++)if(actions.get(i).id().equals(id)){index=i;break;}
                if(index<0||!changed.add(id)||title.isBlank()||title.length()>300||!"planned".equals(actions.get(index).status()))
                    throw new ResponseStatusException(HttpStatus.BAD_REQUEST,"只修改现有待开始步骤");
                var old=actions.get(index);actions.set(index,new GoalRequest.Action(old.id(),title,old.status(),old.feedback()));
            }
            if("revise".equals(request.operation())&&new HashSet<>(actions.stream().map(GoalRequest.Action::title).toList()).size()!=actions.size())
                throw new ResponseStatusException(HttpStatus.BAD_REQUEST,"修改后的步骤不能重复");
            goal.setActionsJson(json.writeValueAsString(actions));
            if(!"revise".equals(request.operation()))goal.setStatus("complete".equals(request.operation())?"completed":"paused");
            goal.setUpdatedAt(LocalDateTime.now());goals.saveAndFlush(goal);
            var entry=new GoalCheckIn();entry.setUser(owner);entry.setGoal(goal);entry.setActionId("$goal");entry.setActionTitle(goal.getTitle());
            entry.setOutcome(outcome);entry.setNote(receipt.getUserMessage().substring(0,Math.min(500,receipt.getUserMessage().length())));
            entry.setSource("conversation");entry.setSourceRequestId(request.requestId());entry.setSessionId(request.sessionId());
            entry.setPreviousStatus("planned");entry.setCreatedAt(LocalDateTime.now());entry.setAppliedGoalVersion(goal.getVersion());
            entry.setCoachingJson(json.writeValueAsString(Map.of("_undo",snapshot,"payloadSha",digest)));
            checkIns.saveAndFlush(entry);
            return Map.of("applied",true,"goal",view(goal),"checkIn",checkInView(entry),"operation",request.operation());
        }catch(ResponseStatusException e){throw e;}catch(Exception e){throw new IllegalStateException("目标调整未保存",e);}
    }

    public boolean verifyModelApproval(String proof,String owner,Long sessionId,String requestId,String source,
            String operation,Long goalId,String actionId,Long version,String outcome) {
        return GoalModelApproval.verify(proof,modelApprovalSecret,modelApprovalSha,owner,sessionId,requestId,source,
                operation,goalId,actionId,version,outcome);
    }

    @Transactional(readOnly=true)
    public Long conversationGoalId(String username,Long sessionId,String requestId) {
        User owner=user(username);
        var current=requestId==null?Optional.<com.vanilo.psych.agent.entity.AgentTurnReceipt>empty()
                :receipts.findByUserAndRequestId(owner,requestId);
        if(current.isPresent()&&!Objects.equals(current.get().getSessionId(),sessionId))return null;
        var previous=current.isPresent()
                ?receipts.findFirstByUserAndSessionIdAndIdLessThanOrderByIdDesc(owner,sessionId,current.get().getId())
                :receipts.findFirstByUserAndSessionIdOrderByIdDesc(owner,sessionId);
        return previous.map(r->referenceGoalId(owner,r)).orElse(null);
    }

    private Long referenceGoalId(User owner, com.vanilo.psych.agent.entity.AgentTurnReceipt receipt) {
        if(receipt.getAssistantReply()==null||receipt.getAssistantReply().isBlank())return null;
        Long id=receipt.getCreatedGoalId();
        Long expectedVersion=0L; // Newly created JPA goals start at version zero.
        if(id==null) {
            var entries=checkIns.findByUserAndSourceRequestId(owner,receipt.getRequestId());
            if(entries.size()!=1||entries.get(0).isUndone())return null;
            id=entries.get(0).getGoal().getId();
            expectedVersion=entries.get(0).getAppliedGoalVersion();
        }
        Long referenceVersion=expectedVersion;
        return goals.findByIdAndUser(id,owner)
                .filter(g->Objects.equals(g.getVersion(),referenceVersion))
                .map(SupportGoal::getId).orElse(null);
    }

    private boolean supportsAdjacentStep(User owner, com.vanilo.psych.agent.entity.AgentTurnReceipt receipt,
                                         GoalSyncRequest request) {
        // Use the complete saved user turn, not a caller-selected substring that
        // could drop a denial/opt-out. IDs, references and version stay server-owned.
        if (receipt.getId() == null || !receipt.getUserMessage().equals(request.quote())
                || !Objects.equals(GoalReferencePolicy.outcome(receipt.getUserMessage()),request.outcome()))
            return false;
        var previous = receipts.findFirstByUserAndSessionIdAndIdLessThanOrderByIdDesc(
                owner, request.sessionId(), receipt.getId());
        if (previous.isEmpty())return false;
        Integer reportedPosition=GoalReferencePolicy.reportedPosition(receipt.getUserMessage());
        if(reportedPosition!=null&&Objects.equals(referenceGoalId(owner,previous.get()),request.goalId())) {
            var goal=owned(request.username(),request.goalId());
            var actions = readActions(goal);
            return reportedPosition<actions.size()&&actions.get(reportedPosition).id().equals(request.actionId())
                    &&"planned".equals(actions.get(reportedPosition).status());
        }
        if (!GoalReferencePolicy.stepQuery(previous.get().getUserMessage())) return false;
        String reply = previous.get().getAssistantReply();
        int position=GoalReferencePolicy.position(previous.get().getUserMessage());
        if(reportedPosition!=null) {
            if(position>=0&&position!=reportedPosition)return false;
            position=reportedPosition;
        }
        List<String> matches = new ArrayList<>();
        boolean positionMismatch=false;
        // Check every owned active step, not only the ID proposed by Runtime.
        for (var goal : goals.findTop50ByUserAndStatusOrderByUpdatedAtDesc(owner,"active")) {
            var actions = readActions(goal);
            for (int index=0; index<actions.size(); index++) {
                var action=actions.get(index);
                if ("planned".equals(action.status()) && GoalReferencePolicy.mentions(reply,action.title())) {
                    matches.add(goal.getId()+":"+action.id());
                    if (position >= 0 && position != index) positionMismatch=true;
                }
            }
        }
        return !positionMismatch && matches.size() == 1 && matches.get(0).equals(request.goalId()+":"+request.actionId());
    }
    public Map<String,Object> undoSync(String username,Long checkInId) {
        var entry=checkIns.findOwnedForCoaching(checkInId,user(username))
                .orElseThrow(()->new ResponseStatusException(HttpStatus.NOT_FOUND));
        if (!"conversation".equals(entry.getSource())) throw new ResponseStatusException(HttpStatus.BAD_REQUEST,"只撤销对话自动同步记录");
        var goal=entry.getGoal();
        if (entry.isUndone()) return Map.of("goal",view(goal),"checkIn",checkInView(entry));
        if (!Objects.equals(goal.getVersion(),entry.getAppliedGoalVersion()))
            throw new ResponseStatusException(HttpStatus.CONFLICT,"目标已有后续调整，请在目标页面核对后修改");
        try {
            if(entry.getCoachingJson()!=null) {
                var snapshot=json.readTree(entry.getCoachingJson()).path("_undo");
                if(snapshot.isObject()) {
                    goal.setActionsJson(snapshot.path("actionsJson").asText());
                    goal.setStatus(snapshot.path("status").asText());
                    goal.setCompletionCriteria(snapshot.path("completionCriteria").asText());
                }
            }
            var actions=readActions(goal);
            if(Set.of("revised","goal_completed","paused").contains(entry.getOutcome())) {
                goal.setUpdatedAt(LocalDateTime.now());goals.saveAndFlush(goal);
                entry.setUndone(true);checkIns.saveAndFlush(entry);
                return Map.of("goal",view(goal),"checkIn",checkInView(entry));
            }
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
    private List<GoalRequest.Action> readActions(SupportGoal goal) {
        try {
            return json.readValue(goal.getActionsJson(), new TypeReference<ArrayList<GoalRequest.Action>>() {});
        } catch (com.fasterxml.jackson.core.JsonProcessingException e) {
            throw new IllegalStateException("行动记录读取失败", e);
        }
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
