package com.vanilo.psych.agent.service;

import com.vanilo.psych.agent.dto.UserProfileResponse;
import com.vanilo.psych.agent.dto.UserProfileUpdateRequest;
import com.vanilo.psych.agent.entity.User;
import com.vanilo.psych.agent.entity.UserProfile;
import com.vanilo.psych.agent.repository.UserProfileMapper;
import com.vanilo.psych.agent.repository.UserMapper;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.time.LocalDateTime;
import java.util.List;
import java.util.function.Consumer;

@Service
public class UserProfileService {
    private final UserMapper userMapper;
    private final UserProfileMapper userProfileMapper;

    public UserProfileService(UserMapper userMapper, UserProfileMapper userProfileMapper) {
        this.userMapper = userMapper;
        this.userProfileMapper = userProfileMapper;
    }

    public UserProfileResponse getProfile(String username) {
        User user = findUser(username);
        UserProfile profile = getOrCreateProfile(user);
        return toResponse(profile);
    }

    public List<UserProfileResponse> listAllProfiles() {
        return userMapper.findAll()
                .stream()
                .map(user -> toResponse(getOrCreateProfile(user)))
                .toList();
    }

    @Transactional
    public UserProfileResponse updateProfile(String username, UserProfileUpdateRequest request) {
        User user = findUser(username);
        UserProfile profile = getOrCreateProfile(user);
        updateIfPresent(request.getProfileSummary(), profile::setProfileSummary);
        updateIfPresent(request.getConcerns(), profile::setConcerns);
        updateIfPresent(request.getPreferences(), profile::setPreferences);
        updateIfPresent(request.getCopingStrategies(), profile::setCopingStrategies);
        updateIfPresent(request.getRiskSignals(), profile::setRiskSignals);
        updateIfPresent(request.getSupportGoals(), profile::setSupportGoals);
        profile.setUpdatedAt(LocalDateTime.now());
        userProfileMapper.update(profile);
        return toResponse(profile);
    }

    UserProfile getOrCreateProfile(User user) {
        return userProfileMapper.findByUser(user).orElseGet(() -> {
            UserProfile profile = new UserProfile();
            profile.setUser(user);
            profile.setProfileSummary("暂无稳定画像");
            profile.setConcerns("暂无");
            profile.setPreferences("暂无");
            profile.setCopingStrategies("暂无");
            profile.setRiskSignals("暂无");
            profile.setSupportGoals("暂无");
            profile.setUpdatedAt(LocalDateTime.now());
            userProfileMapper.insert(profile);
            return profile;
        });
    }

    UserProfileResponse toResponse(UserProfile profile) {
        return new UserProfileResponse(
                profile.getUser().getUsername(),
                profile.getProfileSummary(),
                profile.getConcerns(),
                profile.getPreferences(),
                profile.getCopingStrategies(),
                profile.getRiskSignals(),
                profile.getSupportGoals(),
                profile.getUpdatedAt()
        );
    }

    private User findUser(String username) {
        return userMapper.findByUsername(username).orElseThrow(() -> new RuntimeException("用户不存在！"));
    }

    private void updateIfPresent(String value, Consumer<String> setter) {
        if (value != null) {
            setter.accept(value);
        }
    }
}
