package com.vanilo.psych.agent.dto;

import lombok.AllArgsConstructor;
import lombok.Data;
import lombok.NoArgsConstructor;

import java.util.HashMap;
import java.util.Map;

/** Layered memory written back by the runtime after it has indexed a turn. Blank fields keep the old value. */
@Data
@NoArgsConstructor
@AllArgsConstructor
public class MemoryUpdateRequest {
    private String sessionSummary;
    private String mediumTermSummary;
    private String longTermMemory;
    private Map<String, String> profilePatch = new HashMap<>();
}
