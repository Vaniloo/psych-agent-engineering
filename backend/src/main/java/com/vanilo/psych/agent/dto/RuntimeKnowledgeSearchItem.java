package com.vanilo.psych.agent.dto;

import com.fasterxml.jackson.annotation.JsonIgnoreProperties;
import com.fasterxml.jackson.annotation.JsonProperty;
import lombok.Data;

@Data
@JsonIgnoreProperties(ignoreUnknown = true)
public class RuntimeKnowledgeSearchItem {
    private String id;
    @JsonProperty("document_id")
    private String documentId;
    private String content;
    private String category;
    private String source;
    private Integer rank;
    @JsonProperty("relevance_score")
    private Double relevanceScore;
    @JsonProperty("confidence_label")
    private String confidenceLabel;
    @JsonProperty("match_reason")
    private String matchReason;

    public KnowledgeSearchResponse toResponse() {
        return new KnowledgeSearchResponse(
                content,
                category,
                source,
                documentId == null || documentId.isBlank() ? id : documentId,
                rank,
                relevanceScore,
                confidenceLabel,
                matchReason
        );
    }
}
