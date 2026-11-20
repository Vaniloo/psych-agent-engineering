package com.vanilo.psych.agent.service;

import com.vanilo.psych.agent.entity.UserModelApi;
import com.vanilo.psych.agent.repository.UserModelApiMapper;
import com.vanilo.psych.agent.repository.UserMapper;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.http.HttpStatus;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;
import org.springframework.web.server.ResponseStatusException;

import javax.crypto.Cipher;
import javax.crypto.spec.GCMParameterSpec;
import javax.crypto.spec.SecretKeySpec;
import java.net.URI;
import java.nio.charset.StandardCharsets;
import java.security.SecureRandom;
import java.util.*;

@Service
public class UserModelApiService {
    private final UserModelApiMapper settings;
    private final UserMapper users;
    private final AgentRuntimeClient runtime;
    private final byte[] encryptionKey;

    public UserModelApiService(UserModelApiMapper settings, UserMapper users, AgentRuntimeClient runtime,
            @Value("${MODEL_SETTINGS_ENCRYPTION_KEY:}") String encryptionKey) {
        this.settings = settings; this.users = users; this.runtime = runtime;
        this.encryptionKey = encryptionKey.isBlank() ? null : Base64.getDecoder().decode(encryptionKey);
        if (this.encryptionKey != null && this.encryptionKey.length != 32)
            throw new IllegalStateException("模型配置加密密钥需要32字节");
    }

    public record Input(String baseUrl, String model, @com.fasterxml.jackson.annotation.JsonProperty(access = com.fasterxml.jackson.annotation.JsonProperty.Access.WRITE_ONLY) String apiKey) {
        @Override public String toString() { return "ModelApiInput[redacted]"; }
    }

    private Long userId(String username) {
        return users.findByUsername(username).orElseThrow(() -> error("用户不存在")).getId();
    }

    public Map<String, Object> view(String username) {
        var row = settings.findById(userId(username)).orElse(null);
        return Map.of("configured", row != null, "baseUrl", row == null ? "https://api.deepseek.com" : row.getBaseUrl(),
                "model", row == null ? "deepseek-flash" : row.getModel(), "keyConfigured", row != null,
                "available", encryptionKey != null);
    }

    @Transactional
    public Map<String, Object> save(String username, Input input) {
        requireEncryption(); var row = candidate(username, input);
        settings.upsert(row); return view(username);
    }

    @Transactional
    public void reset(String username) { settings.deleteById(userId(username)); }

    public Object test(String username, Input input) {
        requireEncryption(); return runtime.testModelApi(connection(candidate(username, input)));
    }

    /** Internal authenticated service only. Never serialize this into public context or metrics. */
    public Map<String, Object> connection(String username) {
        var row = settings.findById(userId(username)).orElse(null);
        return row == null ? Map.of("enabled", false) : connection(row);
    }

    private Map<String, Object> connection(UserModelApi row) {
        requireEncryption();
        return Map.of("enabled", true, "baseUrl", row.getBaseUrl(), "model", row.getModel(),
                "apiKey", crypt(row.getEncryptedKey(), row.getUserId(), false));
    }

    private UserModelApi candidate(String username, Input input) {
        if (input == null) throw error("请填写模型API配置");
        String base = normalizeBaseUrl(input.baseUrl());
        String model = input.model() == null ? "" : input.model().strip();
        if (model.isBlank() || model.length() > 160 || model.chars().anyMatch(Character::isISOControl))
            throw error("请填写有效的模型名，最多160字");
        Long id = userId(username);
        var previous = settings.findById(id).orElse(null);
        String key = input.apiKey() == null ? "" : input.apiKey().strip();
        String cipher;
        if (key.isBlank()) {
            if (previous == null || !previous.getBaseUrl().equals(base))
                throw error("新建配置或更换接口地址时，请重新填写API Key");
            cipher = previous.getEncryptedKey();
        } else {
            if (key.length() > 4096 || key.chars().anyMatch(Character::isISOControl)) throw error("API Key格式不正确");
            cipher = crypt(key, id, true);
        }
        var row = new UserModelApi(); row.setUserId(id); row.setBaseUrl(base); row.setModel(model); row.setEncryptedKey(cipher);
        return row;
    }

    public static String normalizeBaseUrl(String value) {
        try {
            String base = Objects.requireNonNull(value).strip().replaceAll("/+$", "");
            URI uri = URI.create(base);
            if (base.length() > 500 || !"https".equals(uri.getScheme()) || uri.getHost() == null
                    || uri.getUserInfo() != null || uri.getQuery() != null || uri.getFragment() != null
                    || (uri.getPort() != -1 && uri.getPort() != 443)) throw error("请输入公开的HTTPS接口地址");
            if (base.endsWith("/chat/completions")) base = base.substring(0, base.length() - "/chat/completions".length());
            return base;
        } catch (Exception e) { throw error("请输入公开的HTTPS接口地址，不包含密钥或查询参数"); }
    }

    private String crypt(String value, Long userId, boolean encrypt) {
        try {
            Cipher cipher = Cipher.getInstance("AES/GCM/NoPadding");
            byte[] nonce, body;
            if (encrypt) { nonce = new byte[12]; new SecureRandom().nextBytes(nonce); body = value.getBytes(StandardCharsets.UTF_8); }
            else { byte[] packed = Base64.getDecoder().decode(value); nonce = Arrays.copyOfRange(packed, 0, 12); body = Arrays.copyOfRange(packed, 12, packed.length); }
            cipher.init(encrypt ? Cipher.ENCRYPT_MODE : Cipher.DECRYPT_MODE, new SecretKeySpec(encryptionKey, "AES"), new GCMParameterSpec(128, nonce));
            cipher.updateAAD(userId.toString().getBytes(StandardCharsets.UTF_8));
            byte[] result = cipher.doFinal(body);
            if (!encrypt) return new String(result, StandardCharsets.UTF_8);
            byte[] packed = Arrays.copyOf(nonce, nonce.length + result.length); System.arraycopy(result, 0, packed, nonce.length, result.length);
            return Base64.getEncoder().encodeToString(packed);
        } catch (Exception e) { throw new ResponseStatusException(HttpStatus.SERVICE_UNAVAILABLE, "模型密钥暂时无法读取，请重新保存配置"); }
    }

    private void requireEncryption() {
        if (encryptionKey == null) throw new ResponseStatusException(HttpStatus.SERVICE_UNAVAILABLE, "管理员尚未配置模型密钥加密服务");
    }
    private static ResponseStatusException error(String text) { return new ResponseStatusException(HttpStatus.BAD_REQUEST, text); }
}
