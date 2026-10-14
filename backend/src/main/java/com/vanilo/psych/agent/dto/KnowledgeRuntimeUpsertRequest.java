package com.vanilo.psych.agent.dto;

import com.fasterxml.jackson.annotation.JsonProperty;
import lombok.AllArgsConstructor;
import lombok.Data;
import lombok.NoArgsConstructor;
import java.util.Map;

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
    private Map<String,String> metadata=Map.of();
    public KnowledgeRuntimeUpsertRequest(String documentId,String content,String category,String source,int chunkSize,int overlap) {
        this(documentId,content,category,source,chunkSize,overlap,Map.of());
    }
}
