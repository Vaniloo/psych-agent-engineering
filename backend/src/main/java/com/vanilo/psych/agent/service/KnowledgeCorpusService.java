package com.vanilo.psych.agent.service;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.vanilo.psych.agent.dto.*;
import com.vanilo.psych.agent.entity.KnowledgeDocument;
import com.vanilo.psych.agent.repository.KnowledgeDocumentMapper;
import org.springframework.stereotype.Service;
import org.springframework.web.server.ResponseStatusException;
import org.springframework.http.HttpStatus;
import java.time.LocalDateTime;
import java.util.*;

/** Stable corpus IDs, SQL source first, explicit retryable indexing status. */
@Service
public class KnowledgeCorpusService {
    private final KnowledgeDocumentMapper repository;
    private final AgentRuntimeClient runtime;
    private final ObjectMapper json;
    public KnowledgeCorpusService(KnowledgeDocumentMapper repository,AgentRuntimeClient runtime,ObjectMapper json) {
        this.repository=repository;this.runtime=runtime;this.json=json;
    }
    public Map<String,Object> upsert(KnowledgeCorpusRequest request) {
        if(!KnowledgeSourcePolicy.accepts(request.source(), request.metadata()))
            throw new ResponseStatusException(HttpStatus.BAD_REQUEST,"语料来源、路径与许可必须符合已核对的来源策略");
        if(request.metadata().values().stream().anyMatch(v->v==null||v.length()>500))
            throw new ResponseStatusException(HttpStatus.BAD_REQUEST,"需要有效许可及有界元数据");
        String metadata;
        try {metadata=json.writeValueAsString(new TreeMap<>(request.metadata()));}
        catch(Exception e) {throw new ResponseStatusException(HttpStatus.BAD_REQUEST,"无效元数据");}
        var previous=repository.findById(request.documentId());
        var document=previous.orElseGet(()->new KnowledgeDocument(request.documentId(),request.content(),request.category(),request.source(),LocalDateTime.now()));
        boolean unchanged=previous.isPresent()&&Objects.equals(document.getContent(),request.content())
                &&Objects.equals(document.getMetadataJson(),metadata)&&Objects.equals(document.getSource(),request.source())
                &&Objects.equals(document.getCategory(),request.category());
        document.setContent(request.content());document.setCategory(request.category());document.setSource(request.source());document.setMetadataJson(metadata);
        // Source is committed before indexing. Index failure stays visible and
        // retrying this stable ID does not duplicate the source row.
        repository.upsert(document);
        try {
            int chunks=runtime.upsertKnowledge(new KnowledgeRuntimeUpsertRequest(document.getId(),document.getContent(),document.getCategory(),document.getSource(),450,80,request.metadata()));
            return Map.of("documentId",document.getId(),"sourceSaved",true,"indexed",true,"chunkCount",chunks,"unchanged",unchanged);
        } catch(RuntimeException e) {
            return Map.of("documentId",document.getId(),"sourceSaved",true,"indexed",false,"reason","runtime_index_pending","unchanged",unchanged);
        }
    }
    public List<Map<String,Object>> catalog() {
        return repository.findAll().stream().filter(d->d.getId().startsWith("corpus-")).map(d-> {
            Map<String,Object> row=new LinkedHashMap<>();row.put("documentId",d.getId());row.put("source",d.getSource());row.put("category",d.getCategory());
            row.put("content",d.getContent());row.put("metadataJson",Objects.toString(d.getMetadataJson(),"{}"));return row;
        }).toList();
    }
}
