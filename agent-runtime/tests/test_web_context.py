import hashlib
import socket
from dataclasses import replace
from unittest.mock import AsyncMock

import pytest

from psych_agent_runtime.config import Settings
from psych_agent_runtime.web_context import WebContextPipeline
from psych_agent_runtime.web_context_filter import WebContextFilter, validate_scores
from psych_agent_runtime.web_filter_contract import CONTRACT, MODEL, REVISION
from psych_agent_runtime.web_reader import PublicResolver, chunk_text, validate_destination


def test_page_chunks_are_verifiable_source_slices_with_overlap():
    text = '甲乙丙丁。'*500
    rows = chunk_text(text)
    assert len(rows) > 1 and rows[0]['end'] > rows[1]['start']
    for r in rows:
        assert text[r['start']:r['end']] == r['text']
        assert hashlib.sha256(r['text'].encode()).hexdigest() == r['sha256']


@pytest.mark.parametrize('url', ['file:///etc/passwd', 'http://a:b@example.org/',
    'https://example.org:8088/', 'http://localhost./', 'http://x.localhost/'])
def test_reader_rejects_unsafe_targets_before_connecting(url):
    with pytest.raises(ValueError):
        validate_destination(url)


@pytest.mark.asyncio
async def test_reader_rejects_mixed_public_private_dns_and_pins_numeric_result(monkeypatch):
    import asyncio
    loop = asyncio.get_running_loop()
    resolver = PublicResolver()
    getaddrinfo = AsyncMock(return_value=[
        (socket.AF_INET, socket.SOCK_STREAM, 6, '', ('8.8.8.8', 443)),
        (socket.AF_INET, socket.SOCK_STREAM, 6, '', ('127.0.0.1', 443))])
    monkeypatch.setattr(loop, 'getaddrinfo', getaddrinfo)
    with pytest.raises(OSError):
        await resolver.resolve('external.example', 443)
    getaddrinfo.return_value = getaddrinfo.return_value[:1]
    assert (await resolver.resolve('external.example', 443))[0]['host'] == '8.8.8.8'


def test_foreign_passages_and_inconsistent_probabilities_cannot_enter_filter():
    candidates = [{'id': 'a'}]
    value = {'contractVersion': CONTRACT, 'provider': 'laya', 'stage': 'title',
             'model': MODEL, 'revision': REVISION,
             'scores': [{'id': 'a', 'score': 2., 'probabilities': {'0': 0., '1': 0., '2': 1., '3': 0.}}]}
    assert validate_scores(value, 'title', candidates) == {'a': 2.}
    value['scores'][0]['id'] = 'foreign'
    with pytest.raises(ValueError):
        validate_scores(value, 'title', candidates)
    value['scores'][0].update(id='a', score=3.)
    with pytest.raises(ValueError):
        validate_scores(value, 'title', candidates)


@pytest.mark.asyncio
async def test_missing_worker_is_explicit_lexical_fallback_not_model_execution():
    scores, status = await WebContextFilter(replace(Settings(), web_filter_url='',
        web_filter_service_key='')).score('chunk', '猫', [{'id': 'a', 'text': '猫'}], 'answer')
    assert scores['a'] == 3 and status['status'] == 'lexical_fallback'
    assert status['modelExecuted'] is False


@pytest.mark.asyncio
async def test_two_stages_keep_whole_excerpts_with_provenance_and_budget(monkeypatch):
    from psych_agent_runtime import web_context
    pipeline = WebContextPipeline(Settings())
    calls = []
    async def score(stage, query, candidates, purpose):
        calls.append((stage, purpose, len(candidates)))
        return {r['id']: 2. for r in candidates}, {'status': 'ok', 'modelExecuted': True}
    pipeline.filter.score = score
    monkeypatch.setattr(web_context, 'read_page', AsyncMock(return_value={
        'available': True, 'text': ('证据，保留条件和否定。'*500),
        'url': 'https://source.example/page', 'pageTruncated': False}))
    result = await pipeline.run('证据', [{'title': '资料', 'url': 'https://source.example/page',
                                       'snippet': '摘要'}], purpose='role_voice')
    assert [c[0] for c in calls] == ['title', 'chunk']
    assert all(c[1] == 'role_voice' for c in calls)
    assert result['pipeline']['selectedBodyEstimatedTokens'] <= 1400
    assert result['pipeline']['pagesRead'] == 1
    assert result['pipeline']['estimatedBodyTokensSaved'] > 0
    assert result['passages'] and all(len(p['sha256']) == 64 for p in result['passages'])
    assert result['evidenceKind'] == 'page_excerpt_unverified'


@pytest.mark.asyncio
async def test_unreadable_pages_degrade_to_search_snippets_without_claiming_body_read(monkeypatch):
    from psych_agent_runtime import web_context
    pipeline = WebContextPipeline(Settings())
    monkeypatch.setattr(web_context, 'read_page', AsyncMock(return_value={'available': False}))
    result = await pipeline.run('query', [{'title': 'source', 'url': 'https://example.org', 'snippet': 'excerpt'}])
    assert result['pipeline']['pagesRead'] == 0 and result['passages'] == []
    assert result['results'][0]['evidenceKind'] == 'search_snippet_only'
    assert result['pipeline']['chunkStage']['modelExecuted'] is False


@pytest.mark.asyncio
async def test_title_scoring_overlaps_fetch_and_slow_page_cannot_hold_complete_search(monkeypatch):
    import asyncio

    from psych_agent_runtime import web_context
    began = asyncio.Event()
    cancelled = asyncio.Event()

    async def page(url):
        began.set()
        if url.endswith('/slow'):
            try:
                await asyncio.Future()
            finally:
                cancelled.set()
        return {'available': True, 'text': '证据包含完整的条件和否定。', 'url': url, 'pageTruncated': False}

    pipeline = WebContextPipeline(replace(Settings(), web_page_budget_seconds=.03))
    async def score(stage, query, candidates, purpose):
        if stage == 'title':
            await asyncio.wait_for(began.wait(), timeout=.2)
        return {r['id']: 2. for r in candidates}, {'status': 'ok', 'modelExecuted': True}
    pipeline.filter.score = score
    monkeypatch.setattr(web_context, 'read_page', page)
    result = await asyncio.wait_for(pipeline.run('证据', [
        {'title': 'fast', 'url': 'https://source.example/fast', 'snippet': '证据'},
        {'title': 'slow', 'url': 'https://source.example/slow', 'snippet': '证据'}]), timeout=.5)
    assert cancelled.is_set()
    assert result['pipeline']['pagesRead'] == 1 and result['pipeline']['pagesTimedOut'] == 1
    assert result['passages'] and result['pipeline']['chunksScored'] <= 12


@pytest.mark.asyncio
async def test_filter_timeout_opens_circuit_and_skips_second_timeout(monkeypatch):
    import asyncio

    from psych_agent_runtime import web_context_filter
    calls = []
    class SlowClient:
        def __init__(self, **kwargs): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        async def post(self, *args, **kwargs):
            calls.append(1)
            await asyncio.Future()
    monkeypatch.setattr(web_context_filter.httpx, 'AsyncClient', SlowClient)
    filter = WebContextFilter(replace(Settings(), web_filter_url='http://local-worker',
        web_filter_service_key='fixture', web_filter_timeout_seconds=.05))
    rows = [{'id': 'p', 'text': '证据'}]
    _, first = await asyncio.wait_for(filter.score('title', '证据', rows, 'answer'), timeout=.3)
    _, second = await asyncio.wait_for(filter.score('chunk', '证据', rows, 'answer'), timeout=.1)
    assert len(calls) == 1 and first['status'] == 'lexical_fallback'
    assert second['reason'] == 'circuit_open' and not second['modelExecuted']
    async with filter.slot:
        filter.retry_at = 0
        _, busy = await filter.score('title', '证据', rows, 'answer')
        assert busy['reason'] == 'filter_busy' and len(calls) == 1
