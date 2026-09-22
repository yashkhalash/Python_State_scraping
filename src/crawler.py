"""
Shared Crawl4AI-based fetch helper.

This module replaces src/stealth.py's manual Playwright context construction.
Crawl4AI manages its own Playwright browser under the hood (installed via the
`crawl4ai-setup` / `playwright install` step - see README/pyproject comment),
so scrapers no longer drive raw Playwright Page/BrowserContext objects
directly. Instead they get a `CrawlResult` back from `fetch_page()` and do
their own deterministic (regex / BeautifulSoup / JsonCssExtractionStrategy)
parsing on `result.html` / `result.extracted_content`.

IMPORTANT: No LLM extraction strategy is used anywhere in this module or by
any scraper built on top of it. Only `JsonCssExtractionStrategy` (pure
CSS-selector based, deterministic) or plain HTML parsing is used. There is no
Anthropic/Claude API call anywhere in the runtime scraping path.
"""
from __future__ import annotations

import random
import asyncio
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator, Dict, List, Optional, Union

from crawl4ai import AsyncWebCrawler, BrowserConfig, CacheMode, CrawlerRunConfig
from crawl4ai.extraction_strategy import JsonCssExtractionStrategy

# Same stealth values previously hard-coded in src/stealth.py, ported over so
# behavior (UA / viewport / locale / timezone / headers / launch args) is
# unchanged.
DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/127.0.0.0 Safari/537.36"
)

DEFAULT_HEADERS = {
    "Accept-Language": "en-US,en;q=0.9",
    "Sec-Ch-Ua": '"Not)A;Brand";v="99", "Google Chrome";v="127", "Chromium";v="127"',
    "Sec-Ch-Ua-Mobile": "?0",
    "Sec-Ch-Ua-Platform": '"macOS"',
}

STEALTH_LAUNCH_ARGS = [
    "--disable-blink-features=AutomationControlled",
    "--no-sandbox",
    "--disable-dev-shm-usage",
]


def build_browser_config(headless: bool = True) -> BrowserConfig:
    """
    Equivalent of src/stealth.py's create_stealth_context(), expressed as a
    Crawl4AI BrowserConfig. `enable_stealth=True` plus `override_navigator`
    (set per-request in CrawlerRunConfig) replicate the old init-script that
    patched navigator.webdriver / navigator.plugins / navigator.languages.
    """
    return BrowserConfig(
        browser_type="chromium",
        headless=headless,
        user_agent=DEFAULT_USER_AGENT,
        viewport_width=1920,
        viewport_height=1080,
        headers=DEFAULT_HEADERS,
        extra_args=STEALTH_LAUNCH_ARGS,
        enable_stealth=True,
        ignore_https_errors=True,
    )


def build_run_config(
    *,
    schema: Optional[Dict[str, Any]] = None,
    css_selector: Optional[str] = None,
    wait_for: Optional[str] = None,
    js_code: Optional[Union[str, List[str]]] = None,
    session_id: Optional[str] = None,
    js_only: bool = False,
) -> CrawlerRunConfig:
    """
    Build a CrawlerRunConfig for a single fetch. `magic`/`simulate_user`/
    `override_navigator` are Crawl4AI's built-in anti-bot-detection helpers,
    used in addition to the explicit UA/header/launch-arg stealth above.
    """
    extraction_strategy = JsonCssExtractionStrategy(schema) if schema else None
    return CrawlerRunConfig(
        cache_mode=CacheMode.BYPASS,
        extraction_strategy=extraction_strategy,
        css_selector=css_selector,
        wait_for=wait_for,
        # A dedicated, shorter budget for `wait_for` conditions (used to wait
        # for AJAX-populated content on sites like GA's DataTables-driven
        # listing/detail pages). Kept separate from page_timeout so a
        # wait_for condition that never resolves (e.g. against static test
        # fixtures with no matching elements) doesn't consume the full page
        # load budget - it degrades gracefully to whatever HTML was captured.
        wait_for_timeout=10000,
        js_code=js_code,
        js_only=js_only,
        session_id=session_id,
        magic=False,
        simulate_user=True,
        override_navigator=True,
        page_timeout=30000,
        locale="en-US",
        timezone_id="America/New_York",
        wait_until="domcontentloaded",
    )


@asynccontextmanager
async def get_crawler(headless: bool = True) -> AsyncIterator[AsyncWebCrawler]:
    """
    Async context manager yielding a started AsyncWebCrawler, mirroring the
    lifecycle `async with async_playwright() as p: ...` had before. Scrapers
    (and run_scraper.py) should hold ONE crawler instance for the duration of
    a scrape run and pass it into `fetch_page`.
    """
    config = build_browser_config(headless=headless)
    crawler = AsyncWebCrawler(config=config)
    await crawler.start()
    try:
        yield crawler
    finally:
        await crawler.close()


async def fetch_page(
    crawler: AsyncWebCrawler,
    url: str,
    *,
    schema: Optional[Dict[str, Any]] = None,
    css_selector: Optional[str] = None,
    wait_for: Optional[str] = None,
    js_code: Optional[Union[str, List[str]]] = None,
    session_id: Optional[str] = None,
    js_only: bool = False,
):
    """
    Fetch a single page through Crawl4AI and return its CrawlResult.

    - `schema`: a JsonCssExtractionStrategy schema dict (baseSelector + fields).
      When given, `result.extracted_content` is a JSON string of matches.
    - `result.html` / `result.cleaned_html` are always available for scrapers
      that still need to run their own deterministic regex/BeautifulSoup
      parsing (e.g. dynamic "find the table row whose label matches X"
      logic that a static CSS schema cannot express).
    - `session_id`: reuse the same browser tab/session across calls, needed
      for `js_code` driven "click Next" pagination.
    """
    run_config = build_run_config(
        schema=schema,
        css_selector=css_selector,
        wait_for=wait_for,
        js_code=js_code,
        session_id=session_id,
        js_only=js_only,
    )
    result = await crawler.arun(url=url, config=run_config)
    return result


async def click_next_page(
    crawler: AsyncWebCrawler,
    session_id: str,
    next_selector_js: str,
    *,
    wait_for: Optional[str] = None,
):
    """
    Reuse an existing browser session to click a "Next" pagination control
    and return the resulting CrawlResult, replacing the old pattern of
    `await next_btn.click(); await page.wait_for_load_state(...)`.

    `next_selector_js` should be a small JS snippet that finds and clicks the
    next-page control, e.g.:
        "const btn = document.querySelector(\"a:has-text('Next')\"); if (btn) btn.click();"
    Since `:has-text` is not valid native CSS/JS, callers should supply a
    plain JS click (see scrapers for concrete snippets using textContent
    matching instead of Playwright-only pseudo-selectors).
    """
    run_config = build_run_config(
        wait_for=wait_for,
        js_code=next_selector_js,
        session_id=session_id,
        js_only=True,
    )
    result = await crawler.arun(url="javascript:void(0)", config=run_config)
    return result


async def random_delay(min_seconds: float = 1.2, max_seconds: float = 3.0) -> None:
    """Preserved from src/stealth.py so existing call sites keep working."""
    await asyncio.sleep(random.uniform(min_seconds, max_seconds))
