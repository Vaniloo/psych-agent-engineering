package com.vanilo.psych.agent.service;

import com.vanilo.psych.agent.dto.LoginRequest;
import com.vanilo.psych.agent.dto.LoginResponse;
import com.vanilo.psych.agent.dto.RegisterRequest;
import com.vanilo.psych.agent.dto.RegisterResponse;
import com.vanilo.psych.agent.entity.User;
import com.vanilo.psych.agent.repository.UserMapper;
import com.vanilo.psych.agent.security.JwtUtil;
import org.springframework.security.crypto.password.PasswordEncoder;
import org.springframework.stereotype.Service;

@Service
public class UserService {
    private final UserMapper userMapper;
    private final PasswordEncoder passwordEncoder;
    private final JwtUtil jwtUtil;

    public UserService(UserMapper userMapper, PasswordEncoder passwordEncoder, JwtUtil jwtUtil) {
        this.userMapper = userMapper;
        this.passwordEncoder = passwordEncoder;
        this.jwtUtil = jwtUtil;
    }
    public RegisterResponse register(RegisterRequest registerRequest) {
        if (userMapper.findByUsername(registerRequest.getUsername()).isPresent()) {
            throw new RuntimeException("用户已经存在");
        }
        User user = new User();
        user.setUsername(registerRequest.getUsername());
        user.setPassword(passwordEncoder.encode(registerRequest.getPassword()));
        user.setRole("USER");
        userMapper.insert(user);
        return new RegisterResponse(user.getId(), user.getUsername(), user.getRole());
    }

    public LoginResponse login(LoginRequest loginRequest) {
        if (loginRequest.getUsername() == null || loginRequest.getUsername().isBlank()) {
            throw new RuntimeException("用户名不能为空");
        }
        User user = userMapper.findByUsername(loginRequest.getUsername())
                .orElseThrow(() -> new RuntimeException("用户不存在"));
        if (loginRequest.getPassword() == null || !passwordEncoder.matches(loginRequest.getPassword(), user.getPassword())) {
            throw new RuntimeException("密码错误");
        }
        String token = jwtUtil.generateToken(user.getUsername());
        return new LoginResponse(user.getId(), user.getUsername(), user.getRole(), token);
    }
}
