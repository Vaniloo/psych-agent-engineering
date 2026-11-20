package com.vanilo.psych.agent.service;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.vanilo.psych.agent.dto.*;
import com.vanilo.psych.agent.entity.KnowledgeDocument;
import com.vanilo.psych.agent.repository.KnowledgeDocumentMapper;
import org.junit.jupiter.api.Test;
import java.util.*;
import static org.junit.jupiter.api.Assertions.*;
import static org.mockito.Mockito.*;

class KnowledgeCorpusServiceTests {
    @Test
    void retryUsesSameSourceIdAndReportsIndexFailureWithoutClaimingSuccess() {
        var repo=mock(KnowledgeDocumentMapper.class);var runtime=mock(AgentRuntimeClient.class);
        Map<String,KnowledgeDocument> rows=new HashMap<>();
        when(repo.findById(anyString())).thenAnswer(c->Optional.ofNullable(rows.get(c.getArgument(0))));
        when(repo.upsert(any())).thenAnswer(c->{KnowledgeDocument d=c.getArgument(0);rows.put(d.getId(),d);return 1;});
        var service=new KnowledgeCorpusService(repo,runtime,new ObjectMapper());
        var request=new KnowledgeCorpusRequest("corpus-nimh-test","Public text","anxiety","https://www.nimh.nih.gov/health/topics/anxiety-disorders",Map.of("license","public-domain-nimh","title","焦虑"));
        when(runtime.upsertKnowledge(any())).thenThrow(new RuntimeException("offline"));
        assertEquals(false,service.upsert(request).get("indexed"));assertEquals(1,rows.size());
        doReturn(3).when(runtime).upsertKnowledge(any());
        var result=service.upsert(request);assertEquals(true,result.get("indexed"));assertEquals(true,result.get("unchanged"));
        assertEquals(1,rows.size());assertTrue(rows.get(request.documentId()).getMetadataJson().contains("public-domain-nimh"));
    }
    @Test
    void disallowedSourceOrMissingLicenseDoesNotWrite() {
        var repo=mock(KnowledgeDocumentMapper.class);var runtime=mock(AgentRuntimeClient.class);
        var service=new KnowledgeCorpusService(repo,runtime,new ObjectMapper());
        assertThrows(RuntimeException.class,()->service.upsert(new KnowledgeCorpusRequest("corpus-test","text","test","https://example.com/text",Map.of("license","public-domain-nimh"))));
        assertThrows(RuntimeException.class,()->service.upsert(new KnowledgeCorpusRequest("corpus-test","text","test","https://www.nimh.nih.gov/health/topics/depression",Map.of("title","text"))));
        verifyNoInteractions(repo,runtime);
    }
}
