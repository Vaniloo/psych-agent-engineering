from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from pydantic import ValidationError

from psych_agent_runtime.chunking import TokenAwareChunker
from psych_agent_runtime.config import Settings
from psych_agent_runtime.knowledge_corpus import allowed_url, extract_page
from psych_agent_runtime.rag import RagEngine
from psych_agent_runtime.schemas import KnowledgeUpsertRequest


def test_article_extraction_excludes_navigation_images_hotline_and_research():
    title, sections, date = extract_page('''<nav><p>menu text</p></nav>
    <article id="main_content_inner"><h1>Anxiety</h1><aside><li>navigation</li></aside>
    <h2 id="part_1">What is anxiety?</h2><p>Everyday anxiety is common.
    Persistent anxiety may interfere with school, work, and relationships.</p>
    <p>In a crisis call 988.</p><h2>Clinical trials</h2><p>Recruiting research participants
    with anxiety symptoms from many locations around the country.</p></article>
    <footer>Last Reviewed: December 2024</footer>''')
    assert title == 'Anxiety' and date == 'December 2024'
    assert len(sections) == 1
    assert '988' not in str(sections) and 'menu text' not in str(sections)
    with pytest.raises(ValueError):
        extract_page('<main>Different layout; do not accidentally index navigation.</main>')
    assert allowed_url('https://www.nimh.nih.gov/health/topics/depression')
    assert not allowed_url('https://www.nimh.nih.gov.evil.test/health/topics/depression')
    assert not allowed_url('http://127.0.0.1/admin')


def test_licensed_topic_scope_is_data_driven_and_preserves_personal_or_uncovered_queries():
    corpus = {'ids': ['faq', 'anxiety', 'social', 'forged'], 'documents': ['short FAQ', 'text', 'text', 'text'],
        'metadatas': [
            {'source': 'seed:faq'},
            {'document_id': 'a', 'source': 'https://www.nimh.nih.gov/health/topics/anxiety-disorders', 'license': 'public-domain-nimh', 'keywords': '焦虑 担心'},
            {'document_id': 's', 'source': 'https://www.nimh.nih.gov/health/publications/social-anxiety-disorder-more-than-just-shyness', 'license': 'public-domain-nimh', 'keywords': '社交焦虑 被评价'},
            {'document_id': 'f', 'source': 'https://www.nimh.nih.gov.evil.test/health/text', 'license': 'public-domain-nimh', 'keywords': '社交焦虑'},
        ]}
    assert RagEngine._licensed_topic_corpus('什么是社交焦虑？', corpus)['ids'] == ['social']
    assert RagEngine._licensed_topic_corpus('我有社交焦虑，陪我聊聊', corpus) is None
    assert RagEngine._licensed_topic_corpus('什么是行为激活？', corpus) is None


@pytest.mark.asyncio
async def test_metadata_is_on_every_chunk_and_exact_retry_avoids_embedding():
    collection = SimpleNamespace(get=Mock(return_value={'ids': []}), delete=Mock(), upsert=Mock())
    runtime = RagEngine.__new__(RagEngine)
    runtime.settings = Settings(embedding_batch_size=3)
    runtime.chunker = TokenAwareChunker()
    runtime.ollama = SimpleNamespace(embed=AsyncMock(side_effect=lambda texts, **_: [[1., 0.] for _ in texts]))
    runtime._ensure_collection = AsyncMock(return_value=collection)
    runtime._bump_cache_version = AsyncMock()
    request = KnowledgeUpsertRequest(document_id='corpus-test', content='Anxiety affects daily life.\n' * 100,
        source='https://www.nimh.nih.gov/health/topics/anxiety-disorders',
        metadata={'title': '焦虑 / Anxiety', 'license': 'public-domain-nimh'})
    count = await runtime.upsert_document(request)
    saved = collection.upsert.call_args.kwargs
    assert count > 1
    assert all(t.startswith('焦虑 / Anxiety') for t in saved['documents'])
    assert all(m['license'] == 'public-domain-nimh' and m['estimated_tokens'] <= 450 for m in saved['metadatas'])
    collection.get.return_value = {'ids': saved['ids']}
    runtime.ollama.embed.reset_mock(); collection.upsert.reset_mock()
    assert await runtime.upsert_document(request) == count
    runtime.ollama.embed.assert_not_awaited(); collection.upsert.assert_not_called()
    with pytest.raises(ValidationError):
        KnowledgeUpsertRequest(document_id='x', content='text', metadata={'document_id': 'override'})


@pytest.mark.asyncio
async def test_embedding_failure_keeps_previous_revision_untouched():
    collection = SimpleNamespace(get=Mock(return_value={'ids': []}), delete=Mock(), upsert=Mock())
    runtime = RagEngine.__new__(RagEngine)
    runtime.settings = Settings(); runtime.chunker = TokenAwareChunker()
    runtime._ensure_collection = AsyncMock(return_value=collection)
    runtime.ollama = SimpleNamespace(embed=AsyncMock(side_effect=RuntimeError('offline')))
    with pytest.raises(RuntimeError):
        await runtime.upsert_document(KnowledgeUpsertRequest(document_id='corpus-test', content='new text'))
    collection.delete.assert_not_called(); collection.upsert.assert_not_called()
