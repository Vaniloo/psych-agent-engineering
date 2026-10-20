"""Local usefulness scores plus an explicitly labelled, conservative lexical fallback."""

import math
import re
import asyncio
import time

import httpx

from .web_filter_contract import CONTRACT, MODEL, REVISION


def lexical_score(query, text):
    def terms(value):
        value = value.casefold()
        words = re.findall(r'[a-z0-9]+', value)
        for run in re.findall(r'[\u3400-\u9fff]+', value):
            words.extend(run[i:i+2] for i in range(max(1, len(run)-1)))
        return set(words)
    q = terms(query)
    return 3 * len(q & terms(text)) / max(1, len(q))


def validate_scores(value, stage, candidates):
    if (not isinstance(value, dict) or value.get('contractVersion') != CONTRACT or value.get('stage') != stage
            or value.get('provider') != 'laya' or value.get('model') != MODEL
            or value.get('revision') != REVISION):
        raise ValueError('wrong model scoring contract')
    rows = value['scores']
    expected = {r['id'] for r in candidates}
    if len(rows) != len(expected) or {r['id'] for r in rows} != expected:
        raise ValueError('missing, duplicate or foreign passage IDs')
    for row in rows:
        score, probabilities = row['score'], row['probabilities']
        if isinstance(score, bool) or not math.isfinite(score) or not 0 <= score <= 3:
            raise ValueError('invalid usefulness score')
        if set(probabilities) != {'0', '1', '2', '3'}:
            raise ValueError('invalid score probabilities')
        if any(isinstance(v, bool) or not math.isfinite(v) or not 0 <= v <= 1
               for v in probabilities.values()):
            raise ValueError('invalid probability')
        if (abs(sum(probabilities.values()) - 1) > .01
                or abs(sum(int(k)*v for k, v in probabilities.items())-score) > .02):
            raise ValueError('inconsistent score distribution')
    return {r['id']: r['score'] for r in rows}


class WebContextFilter:
    def __init__(self, settings):
        self.settings = settings
        self.retry_at = 0.
        self.slot = asyncio.Lock()

    async def score(self, stage, query, candidates, purpose):
        if not candidates:
            return {}, {'status': 'empty', 'modelExecuted': False}
        settings = self.settings
        reason = 'not_configured'
        started = time.monotonic()
        configured = settings.web_filter_url and settings.web_filter_service_key
        if configured and time.monotonic() < self.retry_at:
            reason = 'circuit_open'
        elif configured and self.slot.locked():
            # Do not enqueue more local inference when the single worker is busy.
            reason = 'filter_busy'
        elif configured:
            try:
                async with self.slot, asyncio.timeout(max(.05, min(6, settings.web_filter_timeout_seconds))), httpx.AsyncClient(
                        timeout=max(.05, min(6, settings.web_filter_timeout_seconds)), trust_env=False) as client:
                    response = await client.post(settings.web_filter_url.rstrip('/')+'/v1/web-context/score',
                        headers={'Authorization': 'Bearer '+settings.web_filter_service_key},
                        json={'contractVersion': CONTRACT, 'stage': stage, 'query': query,
                              'purpose': purpose, 'candidates': [
                                  {'id': r['id'], 'title': r.get('title', '')[:200],
                                   'text': r.get('text', '')[:1400]} for r in candidates]})
                    response.raise_for_status()
                    result = response.json()
                    scores = validate_scores(result, stage, candidates)
                return scores, {'status': 'ok', 'modelExecuted': True, 'model': MODEL,
                                'revision': REVISION, 'calibrated': False,
                                'durationMs': round((time.monotonic()-started)*1000, 2),
                                'workerDurationMs': result.get('durationMs')}
            except (httpx.HTTPError, ValueError, TypeError, KeyError, TimeoutError):
                reason = 'model_unavailable_or_invalid'
                self.retry_at = time.monotonic() + 30
        return {r['id']: lexical_score(query, r.get('title', '')+' '+r.get('text', ''))
                for r in candidates}, {'status': 'lexical_fallback', 'reason': reason,
                                      'modelExecuted': False, 'calibrated': False,
                                      'durationMs': round((time.monotonic()-started)*1000, 2)}
