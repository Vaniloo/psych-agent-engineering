package com.vanilo.psych.agent.service;

import org.springframework.beans.factory.annotation.Value;
import org.springframework.http.HttpStatus;
import org.springframework.stereotype.Component;
import org.springframework.web.server.ResponseStatusException;
import java.util.Arrays;

/** Development preview is opt-in per account, and cannot be enabled globally with '*'. */
@Component
public class RoleStudioAccess {
    @Value("${ROLE_STUDIO_ENABLED:false}") private boolean enabled;
    @Value("${ROLE_STUDIO_USERS:}") private String users="";
    public boolean allowed(String username) {
        return enabled&&username!=null&&Arrays.stream(users.split(",")).map(String::strip)
                .filter(s->!s.equals("*")).anyMatch(username::equals);
    }
    public void require(String username) {
        if(!allowed(username))throw new ResponseStatusException(HttpStatus.NOT_FOUND,"角色工作室实验未开启");
    }
}
