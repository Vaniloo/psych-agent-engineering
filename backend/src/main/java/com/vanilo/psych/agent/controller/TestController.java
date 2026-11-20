package com.vanilo.psych.agent.controller;

import com.vanilo.psych.agent.dto.AnalyzeResponse;
import com.vanilo.psych.agent.repository.UserMapper;
import com.vanilo.psych.agent.service.MailAlertService;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

@RestController
@RequestMapping("/test")
public class TestController {
    private final MailAlertService mailAlertService;
    private final UserMapper userMapper;

    public TestController(MailAlertService mailAlertService, UserMapper userMapper) {
        this.mailAlertService = mailAlertService;
        this.userMapper = userMapper;
    }

    @GetMapping("/ping")
    public String ping() {
        return "pong";
    }
    @GetMapping("/db")
    public String db() {
        return "db ok, users=" + userMapper.count();
    }
    @PostMapping("/mail")
    public String testMail() {
        AnalyzeResponse response = new AnalyzeResponse();
        response.setRisk("high");
        response.setEmotion("低落");
        response.setConfidence(1.0);

        mailAlertService.sendHighRiskEmail("testUser", "测试高风险消息", response);

        return "ok";
    }
}
