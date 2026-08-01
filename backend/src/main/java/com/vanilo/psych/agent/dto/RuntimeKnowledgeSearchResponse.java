package com.vanilo.psych.agent.dto;

import lombok.Data;

import java.util.List;

@Data
public class RuntimeKnowledgeSearchResponse {
    private String query;
    private String category;
    private List<RuntimeKnowledgeSearchItem> results;
}
