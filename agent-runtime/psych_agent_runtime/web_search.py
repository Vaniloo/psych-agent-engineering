"""Read-only public web search through a self-hosted SearXNG instance.

Only queries the host authorized (see web_policy) reach this module. Results are
untrusted snippets: private or local URLs are dropped, at most five sources are
returned, and a short file cache avoids hammering upstream engines.
"""

import asyncio
import hashlib
import ipaddress
import json
import logging
import time
from pathlib import Path
from urllib.parse import urlsplit

import httpx

log = logging.getLogger(__name__)


def public_url(value):
    if not isinstance(value, str):
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
        self.settings = settings
        self.locks = {}

    async def search(self, query, limit=5, refresh=False):
        settings = self.settings
        empty = {'available': False, 'query': query, 'results': [], 'cacheHit': False}
        if not settings or not settings.web_search_enabled:
            return {**empty, 'reason': 'disabled'}
        key = hashlib.sha256(json.dumps(['search-v3', settings.web_search_base_url,
            settings.web_search_engines, settings.web_search_fallback_engines,
            query, limit]).encode()).hexdigest()
        folder = Path(settings.web_search_cache_dir)
        path = folder / (key + '.json')
        if len(self.locks) >= 256:
            self.locks = {k: v for k, v in self.locks.items() if v.locked()}
        async with self.locks.setdefault(key, asyncio.Lock()):
            if not refresh and (cached := self._cached(path)) is not None:
                return cached
            started = time.monotonic()
            try:
                budget = max(.05, min(10, settings.web_provider_timeout_seconds))
                async with asyncio.timeout(budget), httpx.AsyncClient(
                        timeout=budget, trust_env=False, follow_redirects=True) as client:
                    rows, offline, attempts = await search_searxng(client, settings, query, budget)
                seen, hits = set(), []
                for row in rows:
                    if row['url'] not in seen:
                        seen.add(row['url'])
                        hits.append(row)
                    if len(hits) >= limit:
                        break
                result = {'available': True, 'query': query, 'results': hits, 'cacheHit': False,
                          'provider': 'searxng', 'retrievedAt': time.time(), 'searchAttempts': attempts}
                if offline:
                    result['unresponsiveEngines'] = offline
                if not hits:
                    failed = bool(attempts) and all(a['status'] != 'empty' for a in attempts)
                    result.update(available=not failed,
                                  reason='upstream_unavailable' if failed else 'no_results')
                result['durationMs'] = round((time.monotonic() - started) * 1000, 2)
            except (httpx.HTTPError, ValueError, TypeError, KeyError, TimeoutError) as exc:
                log.warning('Web search unavailable (%s)', type(exc).__name__)
                result = {**empty, 'reason': 'provider_unavailable', 'provider': 'searxng'}
            self._save(folder, path, result)
            return result

    @staticmethod
    def _cached(path):
        try:
            cached = json.loads(path.read_text())
            data = cached['result']
            # Public facts change quickly; never keep weather/news results for long.
            ttl = 300 if data['available'] and data['results'] else 60
            if time.time() - cached['savedAt'] < ttl:
                return {**data, 'cacheHit': True,
                        'cacheAgeSeconds': round(time.time() - cached['savedAt'], 2)}
        except (OSError, ValueError, KeyError, TypeError):
            pass
        return None

    @staticmethod
    def _save(folder, path, result):
        try:
            folder.mkdir(parents=True, exist_ok=True)
            pending = path.with_suffix('.tmp')
            pending.write_text(json.dumps({'savedAt': time.time(), 'result': result}, ensure_ascii=False))
            pending.chmod(0o600)
            pending.replace(path)
            # Bound cache growth without retaining browsing history indefinitely.
            old = sorted((p for p in folder.glob('*.json') if len(p.stem) == 64),
                         key=lambda p: p.stat().st_mtime, reverse=True)
            for item in old[256:]:
                item.unlink(missing_ok=True)
        except OSError:
            log.warning('Web search cache unavailable')
