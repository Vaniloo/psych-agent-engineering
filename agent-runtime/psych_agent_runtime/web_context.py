"""Two-stage context control shared by general and character-voice searches."""

import asyncio
import time

from .token_budget import TokenEstimator
from .web_context_filter import WebContextFilter, lexical_score
from .web_reader import chunk_text, read_page

PIPELINE_VERSION = 'web-context-3'


def diverse_rank(rows, scores, query):
    """Preserve the best lexical candidate while the base model is uncalibrated."""
    ranked = sorted(rows, key=lambda r: scores[r['id']], reverse=True)
    lexical = sorted(rows, key=lambda r: lexical_score(query, r.get('title', '')+' '+r['text']), reverse=True)
    if len(ranked) > 1 and lexical:
        guard = lexical[0]
        ranked.remove(guard); ranked.insert(1, guard)
    return ranked


class WebContextPipeline:
    def __init__(self, settings):
        self.settings = settings
        self.filter = WebContextFilter(settings)
        self.estimator = TokenEstimator()

    async def run(self, query, hits, purpose='answer', limit=5):
        started = time.monotonic()
        titles = [{'id': f'p{i}', 'title': r['title'], 'text': r['snippet'], **r}
                  for i, r in enumerate(hits[:10])]
        # Start only two likely pages while titles are scored. No model or page
        # content can change destination validation in the public-only reader.
        fetch_started = time.monotonic()
        tasks = {r['id']: asyncio.create_task(read_page(r['url'])) for r in titles[:2]}
        selected_pages, pages = [], []
        try:
            scores, title_stage = await self.filter.score('title', query, titles, purpose)
            ranked_titles = diverse_rank(titles, scores, query)
            if purpose == 'answer' and titles:
                # Reserve one read for the provider's first result. Exact official
                # page titles can match fewer query terms than an article about them.
                ranked_titles.remove(titles[0])
                ranked_titles.insert(0, titles[0])
            selected_pages = ranked_titles[:3]
            selected_ids = {r['id'] for r in selected_pages}
            for key, task in tasks.items():
                if key not in selected_ids:
                    task.cancel()
            for row in selected_pages:
                tasks.setdefault(row['id'], None)
                if tasks[row['id']] is None:
                    tasks[row['id']] = asyncio.create_task(read_page(row['url']))
            selected_tasks = [tasks[row['id']] for row in selected_pages]
            if selected_tasks:
                await asyncio.wait(selected_tasks, timeout=max(.001,
                    min(8, self.settings.web_page_budget_seconds) - (time.monotonic()-fetch_started)))
            pages = [task.result() if task.done() and not task.cancelled() and task.exception() is None
                     else {'available': False, 'reason': 'page_budget_exhausted'} for task in selected_tasks]
        finally:
            for task in tasks.values():
                if not task.done():
                    task.cancel()
            await asyncio.gather(*tasks.values(), return_exceptions=True)
        fetch_ms = round((time.monotonic()-fetch_started)*1000, 2)
        candidates = []; raw_tokens = 0; readable = 0
        for source, page in zip(selected_pages, pages):
            if not page.get('available'):
                continue
            readable += 1; raw_tokens += self.estimator.count(page['text'])
            all_chunks = chunk_text(page['text'], limit=40)
            # Bound inference without silently inspecting only the beginning of a page.
            lexical = sorted(all_chunks, key=lambda c: lexical_score(query, c['text']), reverse=True)[:3]
            spread = all_chunks[::max(1, len(all_chunks)//2)][-1:]
            inspected = {c['start']: c for c in lexical + spread}
            for i, chunk in enumerate(sorted(inspected.values(), key=lambda c: c['start'])):
                candidates.append({**chunk, 'id': source['id']+f'-c{i}', 'sourceId': source['id'],
                                   'title': source['title'], 'url': page['url'],
                                   'pageTruncated': page['pageTruncated']})
        scores, chunk_stage = await self.filter.score('chunk', query, candidates, purpose)
        ranked = diverse_rank(candidates, scores, query)
        if purpose == 'answer':
            # Give each read page a chance before a single page fills the token budget.
            first_by_page = []
            for page in selected_pages:
                best = next((chunk for chunk in ranked if chunk['sourceId'] == page['id']), None)
                if best:
                    first_by_page.append(best)
            used_ids = {chunk['id'] for chunk in first_by_page}
            ranked = first_by_page + [chunk for chunk in ranked if chunk['id'] not in used_ids]
        passages = []; used = 0
        # Budget complete chunks, never take a prefix that might omit a qualification.
        for chunk in ranked:
            # Style data has a stricter acceptance policy: unrelated creator notes must
            # not become a character's voice. Empty evidence falls back to the card.
            if purpose == 'role_voice' and (not chunk_stage.get('modelExecuted')
                                           or scores[chunk['id']] < 1.5):
                continue
            cost = self.estimator.count(chunk['text']) + self.estimator.count(chunk['url']) + 60
            if used + cost > 1400 or len(passages) >= 4:
                continue
            # Overlap is useful for scoring, but redundant windows waste answer context.
            if any(p['sourceId'] == chunk['sourceId'] and
                   max(p['start'], chunk['start']) < min(p['end'], chunk['end']) for p in passages):
                continue
            passages.append({**chunk, 'usefulness': round(scores[chunk['id']], 4),
                             'evidenceKind': 'page_excerpt_unverified'})
            used += cost
        # Source excerpts are candidates, not a claim that the page or quote is verified.
        results = []
        if purpose == 'answer':
            # Keep provider order and distinguish a returned link from an extracted
            # passage. Reuse the final destination for pages that redirected.
            excerpts = {p['sourceId']: p for p in reversed(passages)}
            seen = set()
            for source in titles:
                p = excerpts.get(source['id'])
                row = ({'title': p['title'], 'url': p['url'], 'snippet': p['text'],
                        'evidenceKind': p['evidenceKind']} if p else
                       {'title': source['title'], 'url': source['url'], 'snippet': source['snippet'],
                        'evidenceKind': 'search_snippet_only'})
                if row['url'] not in seen:
                    results.append(row)
                    seen.add(row['url'])
                if len(results) >= limit:
                    break
        else:
            for p in passages:
                if any(r['url'] == p['url'] for r in results):
                    continue
                results.append({'title': p['title'], 'url': p['url'], 'snippet': p['text'],
                                'evidenceKind': p['evidenceKind']})
        selected_tokens = sum(self.estimator.count(p['text']) for p in passages)
        return {'results': results[:limit], 'passages': passages,
                'evidenceKind': 'page_excerpt_unverified' if passages else 'search_snippet_only',
                'voiceEvidenceStatus': ('candidate_unverified' if passages else 'insufficient')
                    if purpose == 'role_voice' else None,
                'pipeline': {'version': PIPELINE_VERSION, 'candidatePages': len(titles),
                             'pagesAttempted': len(selected_pages), 'pagesRead': readable,
                             'pagesTimedOut': sum(p.get('reason') == 'page_budget_exhausted' for p in pages),
                             'titleAndFetchMs': fetch_ms,
                             'maxScoredChunks': 12,
                             'chunksScored': len(candidates), 'chunksSelected': len(passages),
                             'rawBodyEstimatedTokens': raw_tokens,
                             'selectedBodyEstimatedTokens': selected_tokens,
                             'estimatedBodyTokensSaved': max(0, raw_tokens-selected_tokens),
                             'tokenCounting': 'estimate_not_provider_tokenizer',
                             'coverageLimited': True, 'calibrated': False,
                             'selection': 'laya_ranking_with_lexical_guard' if
                                 chunk_stage.get('modelExecuted') else 'lexical_fallback',
                             'titleStage': title_stage, 'chunkStage': chunk_stage,
                             'durationMs': round((time.monotonic()-started)*1000, 2)}}
