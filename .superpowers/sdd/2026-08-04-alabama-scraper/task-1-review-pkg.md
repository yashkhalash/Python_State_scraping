# Review Package for Task 1

## Commits
a16a6c9 feat: add AlabamaScraper module and unit tests

## Stat
 src/scrapers/__init__.py      |   7 ++-
 src/scrapers/alabama.py       | 129 ++++++++++++++++++++++++++++++++++++++++++
 tests/test_alabama_scraper.py |  88 ++++++++++++++++++++++++++++
 3 files changed, 223 insertions(+), 1 deletion(-)

## Diff
```diff
diff --git a/src/scrapers/__init__.py b/src/scrapers/__init__.py
index 61967ea..58ad710 100644
--- a/src/scrapers/__init__.py
+++ b/src/scrapers/__init__.py
@@ -1 +1,6 @@
-"""Scraper implementations for Georgia Procurement portal."""
+"""Scraper implementations for state procurement portals."""
+
+from src.scrapers.georgia import GeorgiaScraper
+from src.scrapers.alabama import AlabamaScraper
+
+__all__ = ["GeorgiaScraper", "AlabamaScraper"]
diff --git a/src/scrapers/alabama.py b/src/scrapers/alabama.py
new file mode 100644
index 0000000..bc317cc
--- /dev/null
+++ b/src/scrapers/alabama.py
@@ -0,0 +1,129 @@
+import re
+import urllib.parse
+from datetime import datetime
+from typing import List, Optional
+from playwright.async_api import BrowserContext
+from src.storage import BidEvent
+from src.stealth import random_delay
+
+class AlabamaScraper:
+    BASE_URL = "https://www.bidnetdirect.com/alabama"
+
+    def _extract_event_id(self, url: str) -> str:
+        match = re.search(r'/(\d+)(?:\?|$|\/)', url)
+        if match:
+            return match.group(1)
+        return f"AL-{hash(url) & 0xffffffff}"
+
+    async def scrape_all_bids(self, context: BrowserContext, max_pages: Optional[int] = 1) -> List[BidEvent]:
+        page = await context.new_page()
+        try:
+            await page.goto(self.BASE_URL, wait_until="domcontentloaded", timeout=30000)
+            await random_delay(1.0, 2.0)
+
+            results: List[BidEvent] = []
+            current_page = 1
+
+            while True:
+                links = await page.locator("a[href*='/solicitations/open-bids/'], a.solicitation-link").all()
+                bids_to_scrape = []
+                seen_urls = set()
+
+                for link in links:
+                    href = await link.get_attribute("href")
+                    if href:
+                        full_url = urllib.parse.urljoin(self.BASE_URL, href)
+                        if full_url not in seen_urls:
+                            seen_urls.add(full_url)
+                            summary_title = (await link.inner_text()).strip()
+                            bids_to_scrape.append((full_url, summary_title))
+
+                if not bids_to_scrape:
+                    break
+
+                for url, summary_title in bids_to_scrape:
+                    detail_bid = await self._scrape_detail_page(context, url, summary_title)
+                    if detail_bid:
+                        results.append(detail_bid)
+
+                if max_pages and current_page >= max_pages:
+                    break
+
+                next_btn = page.locator("a:has-text('Next'), button:has-text('Next'), a:has-text('>')").first
+                if await next_btn.is_visible() and await next_btn.is_enabled():
+                    current_page += 1
+                    await next_btn.click()
+                    await page.wait_for_load_state("domcontentloaded")
+                    await random_delay(1.0, 2.0)
+                else:
+                    break
+
+            return results
+        finally:
+            await page.close()
+
+    async def _scrape_detail_page(self, context: BrowserContext, url: str, summary_title: str = "") -> Optional[BidEvent]:
+        detail_page = await context.new_page()
+        try:
+            await detail_page.goto(url, wait_until="domcontentloaded", timeout=30000)
+            await random_delay(0.5, 1.5)
+
+            event_id = self._extract_event_id(url)
+            now_str = datetime.now().isoformat()
+
+            async def get_label_value(label_regex: str) -> Optional[str]:
+                try:
+                    rows = await detail_page.locator("tr").all()
+                    for row in rows:
+                        row_text = await row.inner_text()
+                        if re.search(label_regex, row_text, re.IGNORECASE):
+                            cells = await row.locator("td, th").all()
+                            if len(cells) >= 2:
+                                val = (await cells[1].inner_text()).strip()
+                                if val:
+                                    return val
+                            elif len(cells) == 1:
+                                parts = row_text.split(":", 1)
+                                if len(parts) > 1 and parts[1].strip():
+                                    return parts[1].strip()
+                except Exception:
+                    pass
+                return None
+
+            title = None
+            title_elem = detail_page.locator("h1.solicitation-title, h1, h2").first
+            if await title_elem.count() > 0:
+                txt = (await title_elem.inner_text()).strip()
+                if txt:
+                    title = txt
+            if not title:
+                title = summary_title or "Alabama Procurement Event"
+
+            agency = await get_label_value(r'Issuing\s*Agency|Agency|Organization|Department') or "State of Alabama"
+            solicitation_type = await get_label_value(r'Solicitation\s*Type|Event\s*Type|Type') or "Solicitation"
+            status = await get_label_value(r'Status') or "Open"
+            post_date = await get_label_value(r'Publication\s*Date|Issue\s*Date|Post\s*Date|Start\s*Date') or now_str[:10]
+            close_date = await get_label_value(r'Closing\s*Date|Close\s*Date|Due\s*Date|End\s*Date') or now_str
+
+            return BidEvent(
+                event_id=event_id,
+                state="AL",
+                portal_name="Alabama Purchasing Group (BidNet Direct)",
+                title=title,
+                agency=agency,
+                solicitation_type=solicitation_type,
+                status=status,
+                post_date=post_date,
+                close_date=close_date,
+                detail_url=url,
+                contact=None,
+                commodity_codes=[],
+                attachments=[],
+                scraped_at=now_str,
+                updated_at=now_str
+            )
+        except Exception as e:
+            print(f"Error scraping detail page {url}: {e}")
+            return None
+        finally:
+            await detail_page.close()
diff --git a/tests/test_alabama_scraper.py b/tests/test_alabama_scraper.py
new file mode 100644
index 0000000..939ca5f
--- /dev/null
+++ b/tests/test_alabama_scraper.py
@@ -0,0 +1,88 @@
+import pytest
+from playwright.async_api import async_playwright
+from src.scrapers.alabama import AlabamaScraper
+from src.storage import BidEvent
+
+def test_alabama_scraper_initialization():
+    scraper = AlabamaScraper()
+    assert scraper.BASE_URL == "https://www.bidnetdirect.com/alabama"
+
+def test_extract_event_id_standard_numeric():
+    scraper = AlabamaScraper()
+    url = "https://www.bidnetdirect.com/alabama/solicitations/open-bids/road-repair/0000432654?selectProjectLocation="
+    event_id = scraper._extract_event_id(url)
+    assert event_id == "0000432654"
+
+def test_extract_event_id_short_path():
+    scraper = AlabamaScraper()
+    url = "https://www.bidnetdirect.com/alabama/0000432654?"
+    event_id = scraper._extract_event_id(url)
+    assert event_id == "0000432654"
+
+def test_extract_event_id_fallback():
+    scraper = AlabamaScraper()
+    url = "https://www.bidnetdirect.com/alabama/solicitations/open-bids/no-numeric-id"
+    event_id = scraper._extract_event_id(url)
+    assert event_id.startswith("AL-")
+    assert len(event_id) > 3
+
+@pytest.mark.asyncio
+async def test_scrape_all_bids_with_mock_page():
+    scraper = AlabamaScraper()
+
+    list_page_html = """
+    <html>
+      <body>
+        <div class="solicitation-list">
+          <a class="solicitation-link" href="/alabama/solicitations/open-bids/road-resurfacing-project/0000432654?query=1">
+            Road Resurfacing Project
+          </a>
+        </div>
+      </body>
+    </html>
+    """
+
+    detail_page_html = """
+    <html>
+      <body>
+        <h1 class="solicitation-title">Road Resurfacing Project</h1>
+        <table class="details-table">
+          <tr><th>Issuing Agency:</th><td>Alabama Department of Transportation</td></tr>
+          <tr><th>Publication Date:</th><td>08/01/2026</td></tr>
+          <tr><th>Closing Date:</th><td>08/25/2026</td></tr>
+        </table>
+      </body>
+    </html>
+    """
+
+    async with async_playwright() as p:
+        browser = await p.chromium.launch(headless=True)
+        context = await browser.new_context()
+
+        async def handle_route(route):
+            url = route.request.url
+            if "0000432654" in url:
+                await route.fulfill(status=200, body=detail_page_html, content_type="text/html")
+            else:
+                await route.fulfill(status=200, body=list_page_html, content_type="text/html")
+
+        await context.route("**/*", handle_route)
+
+        bids = await scraper.scrape_all_bids(context, max_pages=1)
+        await browser.close()
+
+        assert len(bids) == 1
+        bid = bids[0]
+        assert isinstance(bid, BidEvent)
+        assert bid.event_id == "0000432654"
+        assert bid.state == "AL"
+        assert bid.portal_name == "Alabama Purchasing Group (BidNet Direct)"
+        assert bid.title == "Road Resurfacing Project"
+        assert bid.agency == "Alabama Department of Transportation"
+        assert bid.solicitation_type == "Solicitation"
+        assert bid.status == "Open"
+        assert bid.post_date == "08/01/2026"
+        assert bid.close_date == "08/25/2026"
+        assert bid.contact is None
+        assert bid.commodity_codes == []
+        assert bid.attachments == []

```
