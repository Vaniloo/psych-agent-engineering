package com.vanilo.psych.agent.dto;

import com.fasterxml.jackson.annotation.JsonProperty;
import lombok.AllArgsConstructor;
import lombok.Data;
import lombok.NoArgsConstructor;

@Data
@NoArgsConstructor
@AllArgsConstructor
public class KnowledgeRuntimeUpsertRequest {
    @JsonProperty("document_id")
    private String documentId;
    private String content;
    private String category;
    private String source;
    @JsonProperty("chunk_size")
    private int chunkSize;
    private int overlap;
}
