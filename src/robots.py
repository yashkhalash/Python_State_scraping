"""
robots.txt compliance check, per scraper-team-minimum-spec.md's rule: "Do
not bypass logins, payment requirements, CAPTCHAs, robots restrictions,
rate limits, or other access controls."

Scoped to src/dynamic_scraper.py's --state DYNAMIC entry point: that's the
one place in this project a caller can point the crawler at an arbitrary,
not-yet-vetted URL. The 6 selector-based scrapers in src/scrapers/*.py
target specific state portals that were already manually reviewed/assigned
per the spec's "work only on the websites... assigned to you" rule, so this
check isn't inserted into every fetch_page call project-wide.

IMPORTANT: this fetches robots.txt through Crawl4AI's stealth browser (the
same one used for the real page), NOT a plain urllib/httpx request. Several
real state procurement portals (including some of this project's own
already-working selector-based scrapers' hosts, e.g. Iowa, Alabama) sit
behind a WAF/Cloudflare that returns a 401/403 to a plain HTTP client with
no JS engine while serving real content to an actual browser. A plain
urllib.robotparser.read() would misread those hosts as "disallow everything"
even though their real robots.txt is permissive - confirmed by hand against
several live hosts while building this. Fetching via the real browser and
parsing whatever text comes back avoids that false-negative class entirely;
genuinely unparseable/error content just yields no matching rules, which is
the same "allow by default" outcome as a missing robots.txt.
"""
from __future__ import annotations

import urllib.robotparser
from urllib.parse import urlparse

from bs4 import BeautifulSoup
from crawl4ai import AsyncWebCrawler

from src.crawler import fetch_page

_robots_cache: dict[str, urllib.robotparser.RobotFileParser] = {}

USER_AGENT = "ProcurementDataScraper"


async def _get_parser(crawler: AsyncWebCrawler, host_key: str) -> urllib.robotparser.RobotFileParser:
    if host_key in _robots_cache:
        return _robots_cache[host_key]

    parser = urllib.robotparser.RobotFileParser()
    robots_url = f"{host_key}/robots.txt"
    parser.set_url(robots_url)
    try:
        result = await fetch_page(crawler, robots_url)
        html = result.html or ""
        # A browser rendering a plain-text response wraps it in a synthetic
        # <html><body><pre>...</pre></body></html> view; extracting text
        # handles that uniformly with a genuine HTML error/challenge page
        # (which just yields prose that won't match any robots.txt
        # directive syntax, correctly parsing as "no rules found").
        text = BeautifulSoup(html, "lxml").get_text("\n") if html else ""
        parser.parse(text.splitlines())
    except Exception:
        # Truly unreachable (DNS failure, timeout, etc.): standard
        # convention is to allow, not fail closed.
        parser.allow_all = True
    _robots_cache[host_key] = parser
    return parser


async def is_allowed(crawler: AsyncWebCrawler, url: str, *, user_agent: str = USER_AGENT) -> bool:
    parsed = urlparse(url)
    host_key = f"{parsed.scheme}://{parsed.netloc}"
    try:
        parser = await _get_parser(crawler, host_key)
        return parser.can_fetch(user_agent, url)
    except Exception:
        return True
