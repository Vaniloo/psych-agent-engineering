package com.vanilo.psych.agent.controller;

import com.vanilo.psych.agent.security.InternalServiceAuthenticator;
import com.vanilo.psych.agent.service.UserModelApiService;
import org.springframework.http.CacheControl;
import org.springframework.http.ResponseEntity;
import org.springframework.security.core.Authentication;
import org.springframework.web.bind.annotation.*;
import java.util.Map;

@RestController
public class ModelApiController {
    private final UserModelApiService service;
    private final InternalServiceAuthenticator internal;
    public ModelApiController(UserModelApiService service, InternalServiceAuthenticator internal) { this.service=service; this.internal=internal; }
    private ResponseEntity<Object> response(Object value) { return ResponseEntity.ok().cacheControl(CacheControl.noStore()).body(value); }
    @GetMapping("/model-api") public ResponseEntity<Object> get(Authentication auth) { return response(service.view(auth.getName())); }
    @PutMapping("/model-api") public ResponseEntity<Object> save(Authentication auth, @RequestBody UserModelApiService.Input input) { return response(service.save(auth.getName(),input)); }
    @PostMapping("/model-api/test") public ResponseEntity<Object> test(Authentication auth, @RequestBody UserModelApiService.Input input) { return response(service.test(auth.getName(),input)); }
    @DeleteMapping("/model-api") public ResponseEntity<Object> reset(Authentication auth) { service.reset(auth.getName()); return response(Map.of("reset",true)); }
    @GetMapping("/internal/agent/model-api") public ResponseEntity<Object> connection(@RequestHeader("X-Internal-Service-Token") String token, @RequestParam String username) {
        internal.verify(token); return response(service.connection(username));
    }
}
