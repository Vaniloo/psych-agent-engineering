package com.vanilo.psych.agent.entity;

import lombok.AllArgsConstructor;
import lombok.Data;
import lombok.NoArgsConstructor;

import java.time.LocalDateTime;

@Data
@AllArgsConstructor
@NoArgsConstructor
public class KnowledgeDocument {
    private String id;
    private String content;
    private String category;
    private String source;
    private LocalDateTime createdAt;
    private String metadataJson;
    public KnowledgeDocument(String id,String content,String category,String source,LocalDateTime createdAt) {
        this(id,content,category,source,createdAt,null);
    }
}
