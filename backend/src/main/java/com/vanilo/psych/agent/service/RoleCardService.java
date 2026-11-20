package com.vanilo.psych.agent.service;

import com.vanilo.psych.agent.dto.RoleCardRequest;
import com.vanilo.psych.agent.dto.RoleCardResponse;
import com.vanilo.psych.agent.entity.RoleCard;
import com.vanilo.psych.agent.entity.User;
import com.vanilo.psych.agent.repository.RoleCardMapper;
import com.vanilo.psych.agent.repository.UserMapper;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.time.LocalDateTime;
import java.util.ArrayList;
import java.util.List;

@Service
public class RoleCardService {
    private final RoleCardMapper roleCardMapper;
    private final UserMapper userMapper;

    @org.springframework.beans.factory.annotation.Autowired private RoleStudioAccess studioAccess;

    public RoleCardService(RoleCardMapper roleCardMapper, UserMapper userMapper) {
        this.roleCardMapper = roleCardMapper;
        this.userMapper = userMapper;
    }

    public List<RoleCardResponse> listAvailable(String username) {
        User user = findUser(username);
        List<RoleCard> cards = new ArrayList<>(roleCardMapper.presets());
        cards.addAll(roleCardMapper.byOwner(user));
        return cards.stream().filter(card->!Boolean.TRUE.equals(card.getExperimental())||studioAccess!=null&&studioAccess.allowed(username)).map(card -> toResponse(card, user)).toList();
    }

    @Transactional
    public RoleCardResponse create(String username, RoleCardRequest request) {
        validate(request);
        User user = findUser(username);
        RoleCard card = new RoleCard();
        card.setOwner(user);
        card.setName(request.getName().strip());
        card.setDescription(valueOrDefault(request.getDescription(), "自定义陪伴角色"));
        card.setTone(valueOrDefault(request.getTone(), "温和"));
        card.setResponseStyle(valueOrDefault(request.getResponseStyle(), "简洁直接"));
        card.setCustomInstructions(valueOrDefault(request.getCustomInstructions(), ""));
        card.setForbiddenExpressions(valueOrDefault(request.getForbiddenExpressions(), ""));
        card.setPreset(false);
        card.setCreatedAt(LocalDateTime.now());
        return toResponse(roleCardMapper.save(card), user);
    }

    @Transactional
    public RoleCardResponse activate(String username, Long roleCardId) {
        User user = findUser(username);
        RoleCard card = roleCardMapper.findById(roleCardId)
                .orElseThrow(() -> new RuntimeException("角色卡不存在"));
        if (!card.isPreset() && (card.getOwner() == null || !card.getOwner().getId().equals(user.getId()))) {
            throw new RuntimeException("无权使用该角色卡");
        }
        if(Boolean.TRUE.equals(card.getExperimental())&&(studioAccess==null||!studioAccess.allowed(username)))throw new org.springframework.web.server.ResponseStatusException(org.springframework.http.HttpStatus.NOT_FOUND);
        user.setActiveRoleCard(card);
        userMapper.setActiveCard(user);
        return toResponse(card, user);
    }

    public String buildRolePrompt(String username) {
        User user = findUser(username);
        RoleCard active = user.getActiveRoleCard();
        RoleCard card = active == null ? null : roleCardMapper.findById(active.getId()).orElse(null);
        if(card!=null&&Boolean.TRUE.equals(card.getExperimental())&&studioAccess!=null&&studioAccess.allowed(username))return "角色语气由独立实验配置提供。";
        if (card == null || Boolean.TRUE.equals(card.getExperimental())) {
            return "使用默认的温和、自然、谨慎的心理支持风格。";
        }
        return """
                当前角色卡：%s
                角色说明：%s
                语气：%s
                建议方式：%s
                用户自定义要求：%s
                禁忌表达：%s

                角色卡只能改变表达风格，不能绕过风险识别、安全兜底、禁止诊断等底层规则。
                """.formatted(
                card.getName(),
                card.getDescription(),
                card.getTone(),
                card.getResponseStyle(),
                valueOrDefault(card.getCustomInstructions(), "无"),
                valueOrDefault(card.getForbiddenExpressions(), "无")
        );
    }

    private RoleCardResponse toResponse(RoleCard card, User user) {
        var response=new RoleCardResponse(
                card.getId(),
                card.getName(),
                card.getDescription(),
                card.getTone(),
                card.getResponseStyle(),
                card.getCustomInstructions(),
                card.getForbiddenExpressions(),
                card.isPreset(),
                user.getActiveRoleCard() != null && user.getActiveRoleCard().getId().equals(card.getId())
        );
        response.setExperimental(Boolean.TRUE.equals(card.getExperimental()));response.setHasAvatar(card.getAvatarPng()!=null);return response;
    }

    private User findUser(String username) {
        return userMapper.findByUsername(username).orElseThrow(() -> new RuntimeException("用户不存在"));
    }

    private void validate(RoleCardRequest request) {
        if (request == null || request.getName() == null || request.getName().isBlank()) {
            throw new RuntimeException("角色卡名称不能为空");
        }
    }

    private String valueOrDefault(String value, String defaultValue) {
        return value == null || value.isBlank() ? defaultValue : value.strip();
    }
}
