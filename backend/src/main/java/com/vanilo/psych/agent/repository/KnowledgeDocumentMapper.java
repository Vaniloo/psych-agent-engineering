package com.vanilo.psych.agent.repository;

import com.vanilo.psych.agent.entity.KnowledgeDocument;
import java.util.List;
import java.util.Optional;
import org.apache.ibatis.annotations.*;
import org.springframework.data.domain.Pageable;

@Mapper
public interface KnowledgeDocumentMapper {
    @Select("SELECT * FROM knowledge_document WHERE id = #{id}")
    Optional<KnowledgeDocument> findById(String id);

    @Delete("DELETE FROM knowledge_document WHERE id = #{id}")
    int deleteById(String id);

    @Select("SELECT * FROM knowledge_document")
    List<KnowledgeDocument> findAll();

    @Select("""
            SELECT * FROM knowledge_document
            ORDER BY created_at DESC, id DESC LIMIT #{pageSize} OFFSET #{offset}
            """)
    List<KnowledgeDocument> page(Pageable page);

    @Select("SELECT COUNT(*) FROM knowledge_document")
    long count();

    @Select("SELECT EXISTS(SELECT 1 FROM knowledge_document WHERE id = #{id})")
    boolean existsById(String id);

    @Select("SELECT * FROM knowledge_document WHERE content = #{content} ORDER BY id LIMIT 1")
    Optional<KnowledgeDocument> findFirstByContent(String content);

    @Select("SELECT * FROM knowledge_document WHERE source = #{source}")
    List<KnowledgeDocument> findBySource(String source);

    @Insert("""
            INSERT INTO knowledge_document
                (id, content, category, source, created_at, metadata_json)
            VALUES
                (#{id}, #{content}, #{category}, #{source}, #{createdAt}, #{metadataJson})
            ON DUPLICATE KEY UPDATE
                content = #{content}, category = #{category}, source = #{source}, metadata_json =
                #{metadataJson}
            """)
    int upsert(KnowledgeDocument row);
}
