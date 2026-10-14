package com.vanilo.psych.agent.controller;

import com.vanilo.psych.agent.dto.KnowledgeCorpusRequest;
import com.vanilo.psych.agent.security.InternalServiceAuthenticator;
import com.vanilo.psych.agent.service.KnowledgeCorpusService;
import jakarta.validation.Valid;
import org.springframework.web.bind.annotation.*;
import java.util.*;

@RestController
@RequestMapping("/internal/agent/knowledge")
public class InternalKnowledgeController {
    private final InternalServiceAuthenticator auth;
    private final KnowledgeCorpusService corpus;
    public InternalKnowledgeController(InternalServiceAuthenticator auth,KnowledgeCorpusService corpus) {this.auth=auth;this.corpus=corpus;}
    @GetMapping
    public List<Map<String,Object>> catalog(@RequestHeader("X-Internal-Service-Token") String token) {auth.verify(token);return corpus.catalog();}
    @PostMapping("/upsert")
    public Map<String,Object> upsert(@RequestHeader("X-Internal-Service-Token") String token,@Valid @RequestBody KnowledgeCorpusRequest request) {auth.verify(token);return corpus.upsert(request);}
}
