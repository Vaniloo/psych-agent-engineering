"""General read-only web search with pluggable providers and bounded source snippets."""

import asyncio
import base64
import hashlib
import ipaddress
import json
import logging
import time
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import httpx

from .web_context import PIPELINE_VERSION, WebContextPipeline
from .web_filter_contract import REVISION

log = logging.getLogger(__name__)


def public_url(value):
    if not isinstance(value, str):
        return None
    if value.startswith('https://www.bing.com/ck/a'):
        value = parse_qs(urlsplit(value).query).get('u', [''])[0]
        if value.startswith('a1'):
            try:
                value = base64.urlsafe_b64decode(value[2:] + '=' * (-len(value[2:]) % 4)).decode()
            except (ValueError, UnicodeError):
                return None
    try:
        parsed = urlsplit(value)
        host = parsed.hostname or ''
        if parsed.scheme not in {'http', 'https'} or not host or parsed.username or parsed.password:
            return None
        if host == 'localhost' or host.endswith(('.local', '.internal')):
            return None
        try:
            if not ipaddress.ip_address(host).is_global:
                return None
        except ValueError:
            if '.' not in host:
                return None
        return value[:2000]
    except ValueError:
        return None


class BingResults(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.results = []; self.depth = 0; self.heading = False; self.paragraph = False; self.row = None

    def handle_starttag(self, tag, attrs):
        attr = dict(attrs)
        if tag == 'li':
            if self.depth:
                self.depth += 1
            elif 'b_algo' in attr.get('class', '').split():
                self.depth = 1; self.row = {'title': '', 'url': '', 'snippet': ''}
        if self.depth:
            if tag == 'h2': self.heading = True
            if tag == 'p': self.paragraph = True
            if tag == 'a' and self.heading and not self.row['url']: self.row['url'] = attr.get('href', '')

    def handle_data(self, data):
        if self.depth and self.heading: self.row['title'] += data
        if self.depth and self.paragraph: self.row['snippet'] += data

    def handle_endtag(self, tag):
        if tag == 'h2': self.heading = False
        if tag == 'p': self.paragraph = False
        if tag == 'li' and self.depth:
            self.depth -= 1
            if not self.depth:
                url = public_url(self.row['url'])
                if url and self.row['title'].strip():
                    self.results.append({'title': self.row['title'].strip()[:200], 'url': url,
                                         'snippet': self.row['snippet'].strip()[:700]})
                self.row = None


async def search_searxng(client, settings, query, budget):
    """Try one backup group within the same deadline when no usable source survives."""
    primary = [name.strip() for name in settings.web_search_engines.split(',') if name.strip()]
    backup = [name.strip() for name in settings.web_search_fallback_engines.split(',')
              if name.strip() and name.strip() not in primary]
    groups = [primary, backup] if backup else [primary]
    failures = []
    attempts = []
    deadline = time.monotonic() + budget
    for index, group in enumerate(groups):
        params = {'q': query, 'format': 'json'}
        if group:
            params['engines'] = ','.join(group)
        try:
            # Reserve time for the backup if the primary upstream hangs.
            remaining = deadline - time.monotonic()
            timeout = min(remaining, budget * 0.65) if backup and index == 0 else remaining
            # SearXNG can return finished engines while a slower peer is still
            # pending. Leave room for serializing and receiving those hits.
            params['timeout_limit'] = max(.001, timeout - min(.5, timeout * .15))
            async with asyncio.timeout(timeout):
                response = await client.get(settings.web_search_base_url.rstrip('/') + '/search',
                                            params=params)
            response.raise_for_status()
            payload = response.json()
            if not isinstance(payload, dict) or not isinstance(payload.get('results'), list):
                raise ValueError('invalid SearXNG response')
        except (TimeoutError, httpx.TimeoutException):
            attempts.append({'engines': group, 'status': 'timeout'})
            continue
        offline = payload.get('unresponsive_engines') or []
        failures.extend(pair[:2] for pair in offline if isinstance(pair, list) and len(pair) >= 2)
        results = []
        for row in payload['results'][:30]:
            if not isinstance(row, dict):
                continue
            url = public_url(row.get('url'))
            title = row.get('title')
            if url and isinstance(title, str) and title.strip():
                results.append({'url': url, 'title': title.strip()[:200],
                                'snippet': str(row.get('content') or '')[:700]})
        status = 'ok' if results else 'upstream_errors' if offline else 'empty'
        attempts.append({'engines': group, 'status': status})
        if results:
            return results, failures[:12], attempts
    return [], failures[:12], attempts


class WebSearchService:
    def __init__(self, settings):
        self.settings = settings; self.locks = {}; self.disabled = False
        self.pipeline = WebContextPipeline(settings) if settings else None

    async def search_role_voice(self, name, focus="dialogue", limit=5, refresh=False):
        if not self.settings or not self.settings.web_role_voice_enabled:
            return {'available': False, 'reason': 'role_voice_search_paused', 'results': []}
        # Only the selected card name is sent, never private chat or user memories.
        name = name.strip().split("·")[0].strip()[:80]
        topic = "对话 台词 样例" if focus == "dialogue" else "说话方式 语气 语言风格"
        result = await self.search(f'"{name}" {topic}', limit, refresh, purpose='role_voice')
        # The two-stage query includes the character identity; excerpts remain unverified.
        # Legacy search-only providers retain the old identity guard.
        hits = result.get('results', []) if result.get('pipeline') else [r for r in result.get("results", []) if name.casefold() in
                (r["title"] + " " + r["snippet"]).casefold()] if name else []
        return {**result, "results": hits, "roleName": name, "focus": focus,
                "excludedResults": len(result.get("results", [])) - len(hits),
                "evidenceKind": result.get('evidenceKind', "search_snippets_not_verified_dialogue")}

    async def search(self, query, limit=5, refresh=False, *, purpose='answer'):
        settings = self.settings
        empty = {'available': False, 'query': query, 'results': [], 'cacheHit': False}
        if not settings or not settings.web_search_enabled:
            return {**empty, 'reason': 'disabled'}
        if self.disabled:
            return {**empty, 'reason': 'provider_disabled'}
        provider = settings.web_search_provider
        key = hashlib.sha256(json.dumps(['search-v2', PIPELINE_VERSION, REVISION, provider,
            settings.web_search_base_url, settings.web_search_engines, settings.web_search_fallback_engines,
            settings.web_context_enabled,
            bool(settings.web_filter_url and settings.web_filter_service_key),
            query, limit, purpose]).encode()).hexdigest()
        folder = Path(settings.web_search_cache_dir)
        path = folder / (key + '.json')
        if len(self.locks) >= 256:
            # Bounded workers retain only currently held locks.
            self.locks = {k: v for k, v in self.locks.items() if v.locked()}
        lock = self.locks.setdefault(key, asyncio.Lock())
        async with lock:
            if not refresh:
                try:
                    cached = json.loads(path.read_text())
                    data = cached['result']
                    healthy = (not settings.web_context_enabled or
                               data.get('pipeline', {}).get('chunkStage', {}).get('status') == 'ok')
                    # Public facts change even when a query contains the word
                    # "today". Never keep a weather/price/news result for 24h.
                    ttl = 300 if data['available'] and data['results'] and healthy else 60
                    if time.time() - cached['savedAt'] < ttl:
                        return {**cached['result'], 'cacheHit': True,
                                'cacheAgeSeconds': round(time.time()-cached['savedAt'], 2)}
                except (OSError, ValueError, KeyError, TypeError):
                    pass
            started = time.monotonic()
            try:
                budget = max(.05, min(10, settings.web_provider_timeout_seconds))
                async with asyncio.timeout(budget), httpx.AsyncClient(
                        timeout=budget, trust_env=False, follow_redirects=True,
                        headers={'User-Agent': 'Mozilla/5.0 (compatible; PsychAgentDev/0.1)'}) as client:
                    provider_status = []
                    attempts = []
                    if provider == 'searxng':
                        results, provider_status, attempts = await search_searxng(
                            client, settings, query, budget)
                    elif provider == 'tavily':
                        if not settings.web_search_api_key:
                            return {**empty, 'reason': 'missing_provider_key'}
                        response = await client.post('https://api.tavily.com/search',
                            headers={'Authorization': 'Bearer ' + settings.web_search_api_key},
                            json={'query': query, 'max_results': limit, 'search_depth': 'basic',
                                  'include_answer': False, 'include_raw_content': False})
                        response.raise_for_status()
                        results = [{'title': str(r.get('title', ''))[:200], 'url': public_url(r.get('url')),
                                    'snippet': str(r.get('content', ''))[:700]}
                                   for r in response.json().get('results', [])]
                    elif provider == 'bing_html':
                        async with client.stream('GET', 'https://www.bing.com/search', params={'q': query}) as response:
                            response.raise_for_status(); body = bytearray()
                            async for part in response.aiter_bytes():
                                body.extend(part)
                                if len(body) > 2 * 1024 * 1024:
                                    raise ValueError('search response exceeds limit')
                        parser = BingResults(); parser.feed(body.decode('utf-8', errors='replace')); results = parser.results
                    else:
                        raise ValueError('unsupported web search provider')
                seen = set(); hits = []
                for row in results:
                    if row['url'] and row['url'] not in seen:
                        seen.add(row['url']); hits.append(row)
                    if len(hits) >= (10 if settings.web_context_enabled else limit): break
                result = {'available': True, 'query': query, 'results': hits, 'cacheHit': False,
                          'provider': provider, 'retrievedAt': time.time(),
                          'durationMs': round((time.monotonic() - started) * 1000, 2)}
                result['providerDurationMs'] = round((time.monotonic()-started)*1000, 2)
                if provider_status:
                    result['unresponsiveEngines'] = provider_status
                if attempts:
                    result['searchAttempts'] = attempts
                if not hits:
                    failed = bool(attempts) and all(a['status'] != 'empty' for a in attempts)
                    result.update(available=not failed, reason='upstream_unavailable' if failed else 'no_results')
                elif settings.web_context_enabled:
                    result.update(await self.pipeline.run(query, hits, purpose, limit))
                result['durationMs'] = round((time.monotonic()-started)*1000, 2)
            except (httpx.HTTPError, ValueError, TypeError, KeyError, TimeoutError) as exc:
                if provider == 'tavily' and isinstance(exc, httpx.HTTPStatusError) and exc.response.status_code in {401, 402, 403, 432, 433}:
                    self.disabled = True
                log.warning('Web search unavailable (%s)', type(exc).__name__)
                result = {**empty, 'reason': 'provider_unavailable', 'provider': provider}
            try:
                folder.mkdir(parents=True, exist_ok=True)
                pending = path.with_suffix('.tmp'); pending.write_text(json.dumps({'savedAt': time.time(), 'result': result}, ensure_ascii=False)); pending.chmod(0o600); pending.replace(path)
                # Bound cache growth without retaining browsing history indefinitely.
                old = sorted((p for p in folder.glob('*.json') if len(p.stem)==64 and all(c in '0123456789abcdef' for c in p.stem)), key=lambda p: p.stat().st_mtime, reverse=True)
                for item in old[256:]: item.unlink(missing_ok=True)
            except OSError:
                log.warning('Web search cache unavailable')
            return result
