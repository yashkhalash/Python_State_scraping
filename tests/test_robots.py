"""
Tests for src/robots.py. fetch_page (a real network call via Crawl4AI) is
monkeypatched - no real network access.
"""
from types import SimpleNamespace

import pytest

import src.robots as robots


def _fake_fetch_page_factory(html_by_host: dict):
    async def fake_fetch_page(crawler, url, **kwargs):
        host_key = url.rsplit("/robots.txt", 1)[0]
        return SimpleNamespace(html=html_by_host.get(host_key, ""))
    return fake_fetch_page


@pytest.mark.asyncio
async def test_is_allowed_true_when_no_disallow_rule(monkeypatch):
    monkeypatch.setattr(robots, "_robots_cache", {})
    monkeypatch.setattr(
        robots, "fetch_page", _fake_fetch_page_factory({"https://example.com": "User-agent: *\nAllow: /"})
    )
    assert await robots.is_allowed(object(), "https://example.com/bids") is True


@pytest.mark.asyncio
async def test_is_allowed_false_when_path_disallowed(monkeypatch):
    monkeypatch.setattr(robots, "_robots_cache", {})
    monkeypatch.setattr(
        robots, "fetch_page", _fake_fetch_page_factory({"https://example.com": "User-agent: *\nDisallow: /bids"})
    )
    assert await robots.is_allowed(object(), "https://example.com/bids") is False
    assert await robots.is_allowed(object(), "https://example.com/other") is True


@pytest.mark.asyncio
async def test_is_allowed_extracts_text_from_browser_wrapped_plaintext(monkeypatch):
    """A browser wraps a plain-text robots.txt response in a synthetic
    <html><body><pre>...</pre></body></html> view - this must still be
    read as real rules, not as opaque HTML."""
    monkeypatch.setattr(robots, "_robots_cache", {})
    wrapped_html = (
        "<html><head></head><body><pre>User-agent: *\nDisallow: /</pre></body></html>"
    )
    monkeypatch.setattr(robots, "fetch_page", _fake_fetch_page_factory({"https://example.com": wrapped_html}))
    assert await robots.is_allowed(object(), "https://example.com/bids") is False


@pytest.mark.asyncio
async def test_is_allowed_defaults_true_on_unreachable_host(monkeypatch):
    monkeypatch.setattr(robots, "_robots_cache", {})

    async def raising_fetch_page(crawler, url, **kwargs):
        raise RuntimeError("connection refused")

    monkeypatch.setattr(robots, "fetch_page", raising_fetch_page)
    assert await robots.is_allowed(object(), "https://example.com/bids") is True


@pytest.mark.asyncio
async def test_is_allowed_defaults_true_on_unparseable_error_page(monkeypatch):
    """A genuine HTML error/challenge page (e.g. a Cloudflare JS challenge)
    contains no robots.txt-shaped directives - it should parse as "no
    rules found", the same allow-by-default outcome as a missing
    robots.txt, rather than being treated as an explicit disallow."""
    monkeypatch.setattr(robots, "_robots_cache", {})
    challenge_html = "<html><head><title>Just a moment...</title></head><body>Enable JavaScript</body></html>"
    monkeypatch.setattr(robots, "fetch_page", _fake_fetch_page_factory({"https://example.com": challenge_html}))
    assert await robots.is_allowed(object(), "https://example.com/bids") is True


@pytest.mark.asyncio
async def test_get_parser_is_cached_per_host(monkeypatch):
    monkeypatch.setattr(robots, "_robots_cache", {})
    calls = []

    async def fake_fetch_page(crawler, url, **kwargs):
        calls.append(url)
        return SimpleNamespace(html="User-agent: *\nAllow: /")

    monkeypatch.setattr(robots, "fetch_page", fake_fetch_page)

    await robots.is_allowed(object(), "https://example.com/a")
    await robots.is_allowed(object(), "https://example.com/b")
    assert len(calls) == 1  # second call reused the cached parser
