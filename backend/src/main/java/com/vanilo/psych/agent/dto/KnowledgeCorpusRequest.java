package com.vanilo.psych.agent.dto;

import jakarta.validation.constraints.*;
import java.util.Map;

public record KnowledgeCorpusRequest(
        @NotBlank @Pattern(regexp="corpus-[a-z0-9-]{1,100}") String documentId,
        @NotBlank @Size(max=30000) String content,
        @NotBlank @Size(max=100) String category,
        @NotBlank @Size(max=1000) String source,
        @NotNull @Size(min=1,max=12) Map<String,String> metadata) {}
