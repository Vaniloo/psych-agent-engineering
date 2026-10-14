package com.vanilo.psych.agent.dto;

/** An owned original command, recorded before any agent business write. */
public record AgentTurnBeginRequest(String requestId, String username, Long sessionId, String userMessage) {}
