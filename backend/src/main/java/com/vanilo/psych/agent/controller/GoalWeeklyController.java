package com.vanilo.psych.agent.controller;
import com.vanilo.psych.agent.service.GoalWeeklyService;
import org.springframework.security.core.Authentication;
import org.springframework.web.bind.annotation.*;
import java.util.Map;
@RestController
@RequestMapping("/goals/weekly")
public class GoalWeeklyController {
    private final GoalWeeklyService weekly;
    public GoalWeeklyController(GoalWeeklyService weekly){this.weekly=weekly;}
    @GetMapping public Map<String,Object> overview(Authentication auth,@RequestParam(defaultValue="0") int weekOffset){return weekly.overview(auth.getName(),weekOffset);}
}
