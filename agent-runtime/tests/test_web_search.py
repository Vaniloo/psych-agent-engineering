from dataclasses import replace

import httpx
import pytest


from psych_agent_runtime.config import Settings
from psych_agent_runtime.web_search import WebSearchService





@pytest.mark.asyncio
async def test_default_disabled_never_sends_network_requests(tmp_path):
    s=WebSearchService(replace(Settings(),web_search_enabled=False,web_search_cache_dir=str(tmp_path)))
    assert (await s.search('test'))['reason']=='disabled' and not list(tmp_path.glob('*'))






























@pytest.mark.asyncio
@pytest.mark.parametrize("first_status", ["blocked", "empty", "timeout", "unusable", "ok"])
async def test_searxng_uses_backup_only_without_usable_primary_sources(monkeypatch, tmp_path, first_status):
    from psych_agent_runtime import web_search
    original = httpx.AsyncClient
    engines = []
    source = {"title": "AsyncIO", "url": "https://docs.python.org/3/library/asyncio.html", "content": "Docs"}

    def respond(request):
        group = request.url.params["engines"]
        engines.append(group)
        assert 0 < float(request.url.params["timeout_limit"]) < settings.web_provider_timeout_seconds
        if group == "first" and first_status == "empty":
            return httpx.Response(200, json={"results": []})
        if group == "first" and first_status == "timeout":
            raise httpx.ReadTimeout("upstream timeout")
        if group == "first" and first_status == "blocked":
            return httpx.Response(200, json={"results": [], "unresponsive_engines": [["first", "HTTP error"]]})
        if group == "first" and first_status == "unusable":
            return httpx.Response(200, json={"results": [{"title": "invalid", "url": "file:///tmp"}]})
        return httpx.Response(200, json={"results": [source, source]})

    monkeypatch.setattr(web_search.httpx, "AsyncClient",
                        lambda **kw: original(**kw, transport=httpx.MockTransport(respond)))
    settings = replace(Settings(), web_search_enabled=True, web_search_engines="first",
                       web_search_fallback_engines="first,second", web_search_cache_dir=str(tmp_path))
    service = WebSearchService(settings)
    result = await service.search("asyncio")
    assert engines == (["first"] if first_status == "ok" else ["first", "second"])
    assert result["available"] and [r["url"] for r in result["results"]] == [source["url"]]
    assert result["searchAttempts"][-1]["status"] == "ok"
    assert (await service.search("asyncio"))["cacheHit"]


@pytest.mark.asyncio
@pytest.mark.parametrize("errors, reason", [([], "no_results"), ([["first", "timeout"]], "upstream_unavailable")])
async def test_searxng_distinguishes_empty_from_failed_engines(monkeypatch, tmp_path, errors, reason):
    from psych_agent_runtime import web_search
    original = httpx.AsyncClient
    response = httpx.Response(200, json={"results": [], "unresponsive_engines": errors})
    monkeypatch.setattr(web_search.httpx, "AsyncClient",
                        lambda **kw: original(**kw, transport=httpx.MockTransport(lambda _: response)))
    settings = replace(Settings(), web_search_enabled=True, web_search_engines="first",
                       web_search_fallback_engines="", web_search_cache_dir=str(tmp_path))
    result = await WebSearchService(settings).search("unknown")
    assert result["reason"] == reason and result["available"] == (not errors)
    assert len(result["searchAttempts"]) == 1 and not result["results"]
