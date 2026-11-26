package com.vanilo.psych.agent.service;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.vanilo.psych.agent.dto.AdminMemoryResponse;
import com.vanilo.psych.agent.dto.AgentContextResponse;
import com.vanilo.psych.agent.dto.AgentTurnPersistRequest;
import com.vanilo.psych.agent.dto.ConversationMessageResponse;
import com.vanilo.psych.agent.dto.ConversationSessionResponse;
import com.vanilo.psych.agent.entity.ConversationMemory;
import com.vanilo.psych.agent.entity.ConversationMessage;
import com.vanilo.psych.agent.entity.ConversationSession;
import com.vanilo.psych.agent.entity.User;
import com.vanilo.psych.agent.entity.UserProfile;
import com.vanilo.psych.agent.repository.ConversationMemoryMapper;
import com.vanilo.psych.agent.repository.ConversationMessageMapper;
import com.vanilo.psych.agent.repository.ConversationSessionMapper;
import com.vanilo.psych.agent.repository.UserProfileMapper;
import com.vanilo.psych.agent.repository.UserMapper;

import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.time.LocalDateTime;
import java.util.ArrayList;
import java.util.Collections;
import java.util.List;
import java.util.Map;
import java.util.function.Consumer;

@Service
public class ConversationMemoryService {
    private final com.vanilo.psych.agent.repository.AgentTurnReceiptMapper turnReceipts;
    private final ObjectMapper objectMapper;
    private final UserMapper userMapper;
    private final ConversationMessageMapper conversationMessageMapper;
    private final ConversationSessionMapper conversationSessionMapper;
    private final ConversationMemoryMapper conversationMemoryMapper;
    private final UserProfileMapper userProfileMapper;
    private final UserProfileService userProfileService;

    public ConversationMemoryService(
            com.vanilo.psych.agent.repository.AgentTurnReceiptMapper turnReceipts,
            ObjectMapper objectMapper,
            UserMapper userMapper,
            ConversationMessageMapper conversationMessageMapper,
            ConversationSessionMapper conversationSessionMapper,
            ConversationMemoryMapper conversationMemoryMapper,
            UserProfileMapper userProfileMapper,
            UserProfileService userProfileService) {
        this.turnReceipts = turnReceipts;
        this.objectMapper = objectMapper;
        this.userMapper = userMapper;
        this.conversationMessageMapper = conversationMessageMapper;
        this.conversationSessionMapper = conversationSessionMapper;
        this.conversationMemoryMapper = conversationMemoryMapper;
        this.userProfileMapper = userProfileMapper;
        this.userProfileService = userProfileService;
    }

    public Long resolveSessionId(String username, Long sessionId, String firstMessage) {
        User user = findUser(username);
        return getOrCreateSession(user, sessionId, firstMessage).getId();
    }

    @Transactional(readOnly = true)
    public void requireSavedTurn(String username, String requestId) {
        if (requestId == null || !requestId.matches("[A-Za-z0-9_-]{1,128}")
                || turnReceipts.findByUserAndRequestId(findUser(username), requestId)
                    .filter(r -> !r.getAssistantReply().isBlank()).isEmpty()) {
            throw new org.springframework.web.server.ResponseStatusException(
                    org.springframework.http.HttpStatus.NOT_FOUND, "对话记录不存在");
        }
    }

    public AgentContextResponse getAgentContext(String username, Long sessionId, int messageLimit) {
        User user = findUser(username);
        ConversationSession session = findSession(user, sessionId);
        ConversationMemory memory = getOrCreateMemory(user);
        UserProfile profile = userProfileService.getOrCreateProfile(user);
        int safeLimit = Math.max(1, Math.min(messageLimit, 40));

        List<ConversationMessage> selected = new ArrayList<>(
                conversationMessageMapper.recentInSession(user, session, safeLimit));
        Collections.reverse(selected);

        return new AgentContextResponse(
                username,
                session.getId(),
                valueOrDefault(session.getSummary(), ""),
                valueOrDefault(memory.getSummary(), ""),
                valueOrDefault(memory.getLongTermMemory(), ""),
                userProfileService.toResponse(profile),
                selected.stream().map(this::toMessageResponse).toList());
    }

    @Transactional(readOnly = true)
    public List<Map<String, Object>> recentUserEvidence(String username) {
        User owner = findUser(username);
        return conversationMessageMapper.recentByRole(owner, "user", 24)
                .stream().filter(m -> m.getSession() != null)
                .map(m -> Map.<String, Object>of(
                        "id", "message:" + m.getId(), "sessionId", m.getSession().getId(),
                        "role", "user", "content", limit(m.getContent(), 500),
                        "truncated", m.getContent().length() > 500,
                        "observedAt", m.getCreatedAt().atZone(java.time.ZoneId.systemDefault()).toOffsetDateTime().toString(),
                        "timestampRole", "observation_time", "source", "conversation_message:" + m.getId()))
                .toList();
    }

    @Transactional
    public void rememberAgentTurn(AgentTurnPersistRequest request) {
        if (request == null || request.getUsername() == null || request.getUsername().isBlank()) {
            throw new RuntimeException("username不能为空");
        }
        if (request.getUserMessage() == null || request.getUserMessage().isBlank()) {
            throw new RuntimeException("userMessage不能为空");
        }
        if (request.getAssistantReply() == null || request.getAssistantReply().isBlank()) {
            throw new RuntimeException("assistantReply不能为空");
        }

        if (request.getRequestId() == null
                || request.getRequestId().isBlank()
                || request.getRequestId().length() > 128) {
            throw new IllegalArgumentException("requestId不能为空且不能超过128字符");
        }
        User user =
                userMapper
                        .lockByUsername(request.getUsername())
                        .orElseThrow(() -> new IllegalArgumentException("用户不存在"));
        ConversationSession session = findSession(user, request.getSessionId());
        String fingerprint = fingerprint(request);
        var previous = turnReceipts.lockByUserAndRequestId(user, request.getRequestId());
        if (previous.isPresent() && !previous.get().getAssistantReply().isEmpty()) {
            if (!previous.get().getFingerprint().equals(fingerprint)) {
                throw new org.springframework.web.server.ResponseStatusException(
                        org.springframework.http.HttpStatus.CONFLICT, "requestId已用于不同内容");
            }
            return;
        }
        if (previous.isPresent() && (!java.util.Objects.equals(previous.get().getSessionId(), session.getId())
                || !previous.get().getUserMessage().equals(request.getUserMessage()))) {
            throw new org.springframework.web.server.ResponseStatusException(
                    org.springframework.http.HttpStatus.CONFLICT, "requestId已用于不同内容");
        }
        ConversationMemory memory = getOrCreateMemory(user);
        UserProfile profile = userProfileService.getOrCreateProfile(user);

        saveMessage(user, session, "user", request.getUserMessage());
        saveMessage(user, session, "assistant", request.getAssistantReply());

        updateIfNonBlank(
                request.getSessionSummary(), value -> session.setSummary(limit(value, 1000)));
        updateIfNonBlank(
                request.getMediumTermSummary(), value -> memory.setSummary(limit(value, 1000)));
        updateIfNonBlank(
                request.getLongTermMemory(), value -> memory.setLongTermMemory(limit(value, 1000)));
        applyProfilePatch(profile, request.getProfilePatch());

        LocalDateTime now = LocalDateTime.now();
        session.setUpdatedAt(now);
        memory.setUpdatedAt(now);
        profile.setUpdatedAt(now);
        conversationSessionMapper.update(session);
        conversationMemoryMapper.update(memory);
        userProfileMapper.update(profile);
        var receipt = previous.orElseGet(com.vanilo.psych.agent.entity.AgentTurnReceipt::new);
        receipt.setUser(user);
        receipt.setRequestId(request.getRequestId());
        receipt.setSessionId(session.getId());
        receipt.setFingerprint(fingerprint);
        receipt.setUserMessage(request.getUserMessage());
        receipt.setAssistantReply(request.getAssistantReply());
        if (receipt.getCreatedAt() == null) receipt.setCreatedAt(now);
        receipt.setMemoryStatus("PENDING");
        turnReceipts.save(receipt);
    }

    private String fingerprint(AgentTurnPersistRequest request) {
        try {
            var content = new java.util.TreeMap<String, Object>();
            content.put("session", request.getSessionId());
            content.put("user", request.getUserMessage());
            content.put("assistant", request.getAssistantReply());
            content.put("summary", request.getSessionSummary());
            content.put("medium", request.getMediumTermSummary());
            content.put("long", request.getLongTermMemory());
            content.put(
                    "profile",
                    request.getProfilePatch() == null
                            ? Map.of()
                            : new java.util.TreeMap<>(request.getProfilePatch()));
            return java.util.HexFormat.of()
                    .formatHex(
                            java.security.MessageDigest.getInstance("SHA-256")
                                    .digest(objectMapper.writeValueAsBytes(content)));
        } catch (Exception exception) {
            throw new IllegalStateException(exception);
        }
    }

    public List<ConversationSessionResponse> listSessions(String username) {
        User user = findUser(username);
        return conversationSessionMapper.listOwned(user).stream()
                .map(this::toSessionResponse)
                .toList();
    }

    public List<ConversationMessageResponse> listMessages(String username, Long sessionId) {
        User user = findUser(username);
        ConversationSession session = findSession(user, sessionId);
        return conversationMessageMapper
                .sessionHistory(user, session)
                .stream()
                .map(this::toMessageResponse)
                .toList();
    }

    public List<AdminMemoryResponse> listAdminMemories() {
        return userMapper.findAll().stream().map(this::toAdminMemoryResponse).toList();
    }

    public AdminMemoryResponse getAdminMemory(String username) {
        return toAdminMemoryResponse(findUser(username));
    }

    public List<ConversationMessageResponse> listAdminMessages(String username, Long sessionId) {
        return listMessages(username, sessionId);
    }

    /**
     * Mark an outbox job done and apply the runtime's summary/profile update in the same transaction.
     * Returns false when the job had already been completed (a retry), in which case nothing is applied.
     */
    @Transactional
    public boolean completeMemoryJob(Long jobId, com.vanilo.psych.agent.dto.MemoryUpdateRequest update) {
        var receipt = turnReceipts.findById(jobId).orElseThrow(() ->
                new org.springframework.web.server.ResponseStatusException(
                        org.springframework.http.HttpStatus.NOT_FOUND, "记忆任务不存在"));
        // Same lock order as rememberAgentTurn: user row first, then the job.
        User user = userMapper.lockByUsername(receipt.getUser().getUsername())
                .orElseThrow(() -> new IllegalArgumentException("用户不存在"));
        if (turnReceipts.completePendingMemoryJob(jobId) == 0) {
            return false;
        }
        if (update == null) {
            return true;
        }
        ConversationSession session = findSession(user, receipt.getSessionId());
        ConversationMemory memory = getOrCreateMemory(user);
        UserProfile profile = userProfileService.getOrCreateProfile(user);
        updateIfNonBlank(update.getSessionSummary(), value -> session.setSummary(limit(value, 1000)));
        updateIfNonBlank(update.getMediumTermSummary(), value -> memory.setSummary(limit(value, 1000)));
        updateIfNonBlank(update.getLongTermMemory(), value -> memory.setLongTermMemory(limit(value, 1000)));
        applyProfilePatch(profile, update.getProfilePatch());
        LocalDateTime now = LocalDateTime.now();
        memory.setUpdatedAt(now);
        profile.setUpdatedAt(now);
        conversationSessionMapper.update(session);
        conversationMemoryMapper.update(memory);
        userProfileMapper.update(profile);
        return true;
    }

    private void saveMessage(User user, ConversationSession session, String role, String content) {
        ConversationMessage message = new ConversationMessage();
        message.setUser(user);
        message.setSession(session);
        message.setRole(role);
        message.setContent(limit(content, 4000));
        message.setCreatedAt(LocalDateTime.now());
        conversationMessageMapper.insert(message);
    }

    private void applyProfilePatch(UserProfile profile, Map<String, String> patch) {
        if (patch == null || patch.isEmpty()) {
            return;
        }
        updateIfNonBlank(
                firstPresent(patch, "profile_summary", "profileSummary"),
                value -> profile.setProfileSummary(limit(value, 1000)));
        updateIfNonBlank(patch.get("concerns"), value -> profile.setConcerns(limit(value, 1000)));
        updateIfNonBlank(
                patch.get("preferences"), value -> profile.setPreferences(limit(value, 1000)));
        updateIfNonBlank(
                firstPresent(patch, "coping_strategies", "copingStrategies"),
                value -> profile.setCopingStrategies(limit(value, 1000)));
        updateIfNonBlank(
                firstPresent(patch, "risk_signals", "riskSignals"),
                value -> profile.setRiskSignals(limit(value, 1000)));
    }

    private String firstPresent(Map<String, String> values, String first, String second) {
        String value = values.get(first);
        return value == null ? values.get(second) : value;
    }

    private void updateIfNonBlank(String value, Consumer<String> setter) {
        if (value != null && !value.isBlank()) {
            setter.accept(value.strip());
        }
    }

    private ConversationMemory getOrCreateMemory(User user) {
        return conversationMemoryMapper
                .findByUser(user)
                .orElseGet(
                        () -> {
                            ConversationMemory memory = new ConversationMemory();
                            memory.setUser(user);
                            memory.setSummary("暂无摘要");
                            memory.setLongTermMemory("暂无长期记忆");
                            memory.setUpdatedAt(LocalDateTime.now());
                            conversationMemoryMapper.insert(memory);
                            return memory;
                        });
    }

    private ConversationSession getOrCreateSession(User user, Long sessionId, String firstMessage) {
        if (sessionId != null) {
            return findSession(user, sessionId);
        }
        ConversationSession session = new ConversationSession();
        LocalDateTime now = LocalDateTime.now();
        session.setUser(user);
        session.setTitle(buildSessionTitle(firstMessage));
        session.setSummary("暂无会话摘要");
        session.setCreatedAt(now);
        session.setUpdatedAt(now);
        conversationSessionMapper.insert(session);
        return session;
    }

    private ConversationSession findSession(User user, Long sessionId) {
        if (sessionId == null) {
            throw new RuntimeException("sessionId不能为空");
        }
        return conversationSessionMapper
                .findByIdAndUser(sessionId, user)
                .orElseThrow(() -> new RuntimeException("会话不存在"));
    }

    private ConversationSessionResponse toSessionResponse(ConversationSession session) {
        return new ConversationSessionResponse(
                session.getId(),
                session.getTitle(),
                session.getSummary(),
                session.getCreatedAt(),
                session.getUpdatedAt());
    }

    private ConversationMessageResponse toMessageResponse(ConversationMessage message) {
        return new ConversationMessageResponse(
                message.getId(),
                message.getSession() == null ? null : message.getSession().getId(),
                message.getRole(),
                message.getContent(),
                message.getCreatedAt());
    }

    private AdminMemoryResponse toAdminMemoryResponse(User user) {
        ConversationMemory memory = getOrCreateMemory(user);
        UserProfile profile = userProfileService.getOrCreateProfile(user);
        return new AdminMemoryResponse(
                user.getUsername(),
                memory.getSummary(),
                memory.getLongTermMemory(),
                memory.getUpdatedAt(),
                userProfileService.toResponse(profile),
                conversationSessionMapper.listOwned(user).stream()
                        .map(this::toSessionResponse)
                        .toList());
    }

    private String buildSessionTitle(String message) {
        String title = valueOrDefault(message, "新对话").strip().replaceAll("\\s+", " ");
        if (title.length() > 24) {
            return title.substring(0, 24);
        }
        return title;
    }

    private User findUser(String username) {
        return userMapper
                .findByUsername(username)
                .orElseThrow(() -> new RuntimeException("用户不存在！"));
    }

    private String valueOrDefault(String value, String defaultValue) {
        if (value == null || value.isBlank()) {
            return defaultValue;
        }
        return value;
    }

    private String limit(String value, int maxLength) {
        if (value == null) {
            return "";
        }
        if (value.length() <= maxLength) {
            return value;
        }
        return value.substring(0, maxLength);
    }
}
