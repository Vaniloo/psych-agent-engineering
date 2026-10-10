package com.vanilo.psych.agent.controller;

import com.vanilo.psych.agent.dto.AnalyzeResponse;
import com.vanilo.psych.agent.service.PsychologicalService;

import org.springframework.security.core.Authentication;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

@RestController
@RequestMapping("/chat")
public class ChatController {
    private final PsychologicalService psychologicalService;

    public ChatController(PsychologicalService psychologicalService) {
        this.psychologicalService = psychologicalService;
    }

    @PostMapping("/analyze")
    public AnalyzeResponse analyze(@RequestBody String message, Authentication authentication) {
        String username = authentication.getName();
        return psychologicalService.analyze(message, username);
    }
}
