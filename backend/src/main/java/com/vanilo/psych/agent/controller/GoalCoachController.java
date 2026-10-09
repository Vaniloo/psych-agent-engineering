package com.vanilo.psych.agent.controller;
import com.vanilo.psych.agent.dto.GoalReflectRequest;
import com.vanilo.psych.agent.service.GoalCoachService;
import jakarta.validation.Valid;
import org.springframework.security.core.Authentication;
import org.springframework.web.bind.annotation.*;
import java.util.Map;
@RestController
@RequestMapping("/goals")
public class GoalCoachController {
    private final GoalCoachService coach;
    public GoalCoachController(GoalCoachService coach){this.coach=coach;}
    @PostMapping("/{id}/reflection")
    public Map<String,Object> reflect(Authentication auth,@PathVariable Long id,@Valid @RequestBody GoalReflectRequest request){return coach.reflect(auth.getName(),id,request);}
}
