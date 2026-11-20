package com.vanilo.psych.agent.security;

import com.vanilo.psych.agent.entity.User;
import com.vanilo.psych.agent.repository.UserMapper;
import org.springframework.security.core.userdetails.UserDetails;
import org.springframework.security.core.userdetails.UserDetailsService;
import org.springframework.security.core.userdetails.UsernameNotFoundException;
import org.springframework.stereotype.Service;


@Service
public class CustomUserDetailsService implements UserDetailsService {
    private final UserMapper userMapper;
    public CustomUserDetailsService(UserMapper userMapper){
        this.userMapper = userMapper;
    }
    @Override
    public UserDetails loadUserByUsername(String username){
        User user = userMapper.findByUsername(username).orElseThrow(()-> new UsernameNotFoundException("用户不存在"));

        return org.springframework.security.core.userdetails.User.builder()
                .username(user.getUsername())
                .password(user.getPassword())
                .roles(user.getRole())
                .build();

    }
}
