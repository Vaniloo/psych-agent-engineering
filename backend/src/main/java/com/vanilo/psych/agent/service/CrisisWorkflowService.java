package com.vanilo.psych.agent.service;

import com.vanilo.psych.agent.entity.RiskAlertEvent;
import com.vanilo.psych.agent.repository.RiskAlertEventMapper;
import org.springframework.stereotype.Service;

import java.time.LocalDateTime;

/**
 * Records a crisis event. The reply itself is fixed text from {@link CrisisSupportService}
 * and never goes through a model. A short Redis lock stops a repeated message from writing
 * duplicate rows; if Redis is down the lock allows the write so the event is never lost.
 */
@Service
public class CrisisWorkflowService {
    private final RiskAlertLockService riskAlertLockService;
    private final RiskAlertEventMapper riskAlertEventMapper;

    public CrisisWorkflowService(RiskAlertLockService riskAlertLockService,
                                 RiskAlertEventMapper riskAlertEventMapper) {
        this.riskAlertLockService = riskAlertLockService;
        this.riskAlertEventMapper = riskAlertEventMapper;
    }

    public void handle(String username, String message) {
        if (!riskAlertLockService.tryLock(username, message)) {
            return;
        }
        riskAlertEventMapper.insert(
                new RiskAlertEvent(null, username, message, "high", "RECORDED", LocalDateTime.now()));
    }
}
