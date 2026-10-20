"""Bounded public-page reading. Resolve and validate the actual connection destination."""

import asyncio
import hashlib
import ipaddress
import socket
from urllib.parse import urljoin, urlsplit

import aiohttp
import trafilatura
from aiohttp.abc import AbstractResolver


def validate_destination(url):
    p = urlsplit(url)
    host = (p.hostname or '').rstrip('.').casefold()
    if (p.scheme not in {'http', 'https'} or not host or p.username or p.password
            or p.port not in {None, 80, 443} or host == 'localhost'
            or host.endswith(('.localhost', '.local', '.internal'))):
        raise ValueError('nonpublic destination')
    return host


class PublicResolver(AbstractResolver):
    async def resolve(self, host, port=0, family=socket.AF_INET):
        rows = await asyncio.get_running_loop().getaddrinfo(
            host, port, family=family, type=socket.SOCK_STREAM)
        if not rows or any(not ipaddress.ip_address(r[4][0]).is_global for r in rows):
            raise OSError('nonpublic address')
        # aiohttp connects to these numeric addresses, avoiding a second DNS resolution.
        return [{'hostname': host, 'host': r[4][0], 'port': port, 'family': r[0],
                 'proto': r[2], 'flags': socket.AI_NUMERICHOST} for r in rows]

    async def close(self):
        pass


def chunk_text(text, size=700, overlap=100, limit=8):
    if not 0 <= overlap < size:
        raise ValueError('invalid chunk overlap')
    result = []
    for start in range(0, len(text), size - overlap):
        end = min(start + size, len(text))
        chunk = text[start:end]
        if chunk.strip():
            result.append({'text': chunk, 'start': start, 'end': end,
                           'sha256': hashlib.sha256(chunk.encode()).hexdigest()})
        if end == len(text) or len(result) >= limit:
            break
    return result


async def read_page(url):
    """No cookies, proxy credentials, scripts or challenge bypass. Every redirect is checked."""
    try:
        connector = aiohttp.TCPConnector(resolver=PublicResolver(), use_dns_cache=False)
        async with aiohttp.ClientSession(connector=connector, trust_env=False,
                cookie_jar=aiohttp.DummyCookieJar(), timeout=aiohttp.ClientTimeout(total=7),
                headers={'User-Agent': 'PsychAgentResearch/0.1'}) as session:
            async with asyncio.timeout(8):
                for redirect in range(4):
                    host = validate_destination(url)
                    try:
                        if not ipaddress.ip_address(host).is_global:
                            raise ValueError('nonpublic destination')
                    except ValueError as exc:
                        # Distinguish hostname parsing from a rejected literal IP.
                        if str(exc) == 'nonpublic destination':
                            raise
                    async with session.get(url, allow_redirects=False) as response:
                        if response.status in {301, 302, 303, 307, 308}:
                            if redirect == 3 or not response.headers.get('Location'):
                                raise ValueError('redirect limit')
                            url = urljoin(url, response.headers['Location']); continue
                        response.raise_for_status()
                        kind = response.headers.get('Content-Type', '').split(';')[0].lower()
                        if kind not in {'text/html', 'text/plain', 'application/xhtml+xml'}:
                            raise ValueError('unsupported content type')
                        body = bytearray()
                        async for part in response.content.iter_chunked(32768):
                            body.extend(part)
                            if len(body) > 1024 * 1024:
                                raise ValueError('page exceeds byte limit')
                        html = body.decode(response.charset or 'utf-8', errors='replace')
                        text = (html if kind == 'text/plain' else await asyncio.to_thread(
                            trafilatura.extract, html, include_comments=False,
                            include_tables=True, favor_precision=True)) or ''
                        if not text.strip():
                            raise ValueError('no readable body')
                        return {'available': True, 'url': url, 'text': text[:24000],
                                'pageTruncated': len(text) > 24000}
    except (aiohttp.ClientError, OSError, ValueError, TimeoutError):
        return {'available': False, 'reason': 'page_unreadable'}
    return {'available': False, 'reason': 'redirect_limit'}
