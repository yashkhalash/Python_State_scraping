# Alabama Procurement Scraper Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build and integrate an Alabama procurement scraper using Playwright to extract active bids from BidNet Direct (`https://www.bidnetdirect.com/alabama`) and save them as `BidEvent` JSON models.

**Architecture:** Create `src/scrapers/alabama.py` with `AlabamaScraper` class, update `run_scraper.py` to route `--state AL`, and add unit + E2E tests in `tests/test_alabama_scraper.py` and `tests/test_e2e.py`.

**Tech Stack:** Python 3.12, Playwright, Pydantic, Pytest.

## Global Constraints
- Target URL: `https://www.bidnetdirect.com/alabama`
- Target State: `"AL"`
- Portal Name: `"Alabama Purchasing Group (BidNet Direct)"`
- File output default: `data/output/alabama_bids.json`
- Preserve existing APIs and storage contracts in `src/storage.py` and `src/stealth.py`.

---

### Task 1: Create AlabamaScraper Module and Unit Tests

**Files:**
- Create: `src/scrapers/alabama.py`
- Modify: `src/scrapers/__init__.py`
- Test: `tests/test_alabama_scraper.py`

**Interfaces:**
- Consumes: `src.storage.BidEvent`, `src.storage.BidContact`, `src.storage.CommodityCode`, `src.storage.Attachment`, `src.stealth.random_delay`
- Produces: `AlabamaScraper.scrape_all_bids(context: BrowserContext, max_pages: Optional[int] = 1) -> List[BidEvent]`

- [ ] **Step 1: Write failing unit test for AlabamaScraper HTML parsing logic**

```python
# tests/test_alabama_scraper.py
import pytest
from unittest.mock import AsyncMock, MagicMock
from src.scrapers.alabama import AlabamaScraper
from src.storage import BidEvent

def test_alabama_scraper_extract_event_id():
    scraper = AlabamaScraper()
    url = "https://www.bidnetdirect.com/alabama/solicitations/open-bids/Propane-and-As-Needed-Tank-Repairs-for-Madison-County/0000432654?purchasingGroupId=388530651&origin=1"
    event_id = scraper._extract_event_id(url)
    assert event_id == "0000432654"

def test_alabama_scraper_extract_event_id_fallback():
    scraper = AlabamaScraper()
    url = "https://www.bidnetdirect.com/alabama/solicitations/open-bids/test-bid"
    event_id = scraper._extract_event_id(url)
    assert len(event_id) > 0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_alabama_scraper.py -v`
Expected: FAIL with "ModuleNotFoundError: No module named 'src.scrapers.alabama'"

- [ ] **Step 3: Implement AlabamaScraper class**

```python
# src/scrapers/alabama.py
import re
import urllib.parse
from datetime import datetime
from typing import List, Optional
from playwright.async_api import BrowserContext
from src.storage import BidEvent
from src.stealth import random_delay

class AlabamaScraper:
    BASE_URL = "https://www.bidnetdirect.com/alabama"

    def _extract_event_id(self, url: str) -> str:
        match = re.search(r'/([0-9]{5,12})(?:\?|$)', url)
        if match:
            return match.group(1)
        return f"AL-{abs(hash(url)) & 0xffffffff}"

    async def scrape_all_bids(self, context: BrowserContext, max_pages: Optional[int] = 1) -> List[BidEvent]:
        page = await context.new_page()
        try:
            await page.goto(self.BASE_URL, wait_until="domcontentloaded", timeout=30000)
            await random_delay(1.0, 2.0)

            results: List[BidEvent] = []
            current_page = 1

            while True:
                # Find all solicitation rows or links
                links = await page.locator("a[href*='/solicitations/open-bids/'], a.solicitation-link").all()
                event_items = []
                for link in links:
                    href = await link.get_attribute("href")
                    title = (await link.inner_text()).strip()
                    if href and title:
                        full_url = urllib.parse.urljoin("https://www.bidnetdirect.com", href)
                        if full_url not in [e["url"] for e in event_items]:
                            event_items.append({"title": title, "url": full_url})

                if not event_items:
                    break

                for item in event_items:
                    detail_bid = await self._scrape_detail_page(context, item["url"], item["title"])
                    if detail_bid:
                        results.append(detail_bid)

                if max_pages and current_page >= max_pages:
                    break

                # Pagination check
                next_btn = page.locator("a:has-text('Next'), button:has-text('Next')").first
                if await next_btn.is_visible() and await next_btn.is_enabled():
                    current_page += 1
                    await next_btn.click()
                    await page.wait_for_load_state("domcontentloaded")
                    await random_delay(1.0, 2.0)
                else:
                    break

            return results
        finally:
            await page.close()

    async def _scrape_detail_page(self, context: BrowserContext, url: str, summary_title: str) -> Optional[BidEvent]:
        detail_page = await context.new_page()
        try:
            await detail_page.goto(url, wait_until="domcontentloaded", timeout=30000)
            await random_delay(0.5, 1.5)

            event_id = self._extract_event_id(url)
            now_str = datetime.now().isoformat()

            # Extract fields from detail page
            title = summary_title
            title_elem = detail_page.locator("h1, h2, div.solicitation-title").first
            if await title_elem.count() > 0:
                t_txt = (await title_elem.inner_text()).strip()
                if t_txt:
                    title = t_txt

            # Helper for label extraction
            async def get_label_value(label_regex: str) -> Optional[str]:
                try:
                    elements = await detail_page.locator("tr, div.row, div").all()
                    for elem in elements:
                        text = await elem.inner_text()
                        if re.search(label_regex, text, re.IGNORECASE):
                            parts = text.split("\n")
                            if len(parts) >= 2:
                                return parts[1].strip()
                except Exception:
                    pass
                return None

            location = await get_label_value(r'Location') or "Alabama"
            post_date = await get_label_value(r'Publication\s*Date|Published') or now_str[:10]
            close_date = await get_label_value(r'Closing\s*Date|Due') or now_str

            agency = "State of Alabama"
            if location and location.lower() != "alabama":
                agency = location

            return BidEvent(
                event_id=str(event_id),
                state="AL",
                portal_name="Alabama Purchasing Group (BidNet Direct)",
                title=title,
                agency=agency,
                solicitation_type="Solicitation",
                status="Open",
                post_date=post_date,
                close_date=close_date,
                detail_url=url,
                contact=None,
                commodity_codes=[],
                attachments=[],
                scraped_at=now_str,
                updated_at=now_str
            )
        except Exception as e:
            print(f"Error scraping Alabama detail page {url}: {e}")
            return None
        finally:
            await detail_page.close()
```

Also update `src/scrapers/__init__.py`:
```python
from src.scrapers.georgia import GeorgiaScraper
from src.scrapers.alabama import AlabamaScraper

__all__ = ["GeorgiaScraper", "AlabamaScraper"]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/test_alabama_scraper.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/scrapers/alabama.py src/scrapers/__init__.py tests/test_alabama_scraper.py
git commit -m "feat: add AlabamaScraper module and unit tests"
```

---

### Task 2: Integrate Alabama Scraper into CLI & End-to-End Test

**Files:**
- Modify: `run_scraper.py`
- Modify: `tests/test_e2e.py`

**Interfaces:**
- Consumes: `AlabamaScraper`
- Produces: `run_scraper_pipeline(state="AL", output_path=Path("data/output/alabama_bids.json"))`

- [ ] **Step 1: Write failing test in `tests/test_e2e.py` for state `AL`**

```python
# Modify tests/test_e2e.py
import pytest
from pathlib import Path
from run_scraper import run_scraper_pipeline

@pytest.mark.asyncio
async def test_run_scraper_pipeline_alabama(tmp_path):
    output_file = tmp_path / "alabama_bids.json"
    count = await run_scraper_pipeline(state="AL", output_path=output_file, max_pages=1, headless=True)
    assert output_file.exists()
    assert count >= 0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_e2e.py -v`
Expected: FAIL with `ValueError: Unsupported state: AL`

- [ ] **Step 3: Update `run_scraper.py` to handle state `AL`**

```python
# Modify run_scraper.py
import argparse
import asyncio
from pathlib import Path
from playwright.async_api import async_playwright
from src.stealth import create_stealth_context
from src.scrapers.georgia import GeorgiaScraper
from src.scrapers.alabama import AlabamaScraper
from src.storage import StorageManager

async def run_scraper_pipeline(state: str, output_path: Path, max_pages: int = 1, headless: bool = True) -> int:
    state_upper = state.upper()
    if state_upper not in ["GA", "AL"]:
        raise ValueError(f"Unsupported state: {state}")

    output_path = Path(output_path)
    print(f"Starting scraper for state: {state_upper}")
    
    async with async_playwright() as p:
        context = await create_stealth_context(p, headless=headless)
        
        if state_upper == "GA":
            scraper = GeorgiaScraper()
        elif state_upper == "AL":
            scraper = AlabamaScraper()
            
        bids = await scraper.scrape_all_bids(context, max_pages=max_pages)
        await context.close()

        storage = StorageManager()
        storage.upsert_bids(output_path, bids)
        print(f"Successfully scraped and saved {len(bids)} bids to {output_path}")
        return len(bids)

def main():
    parser = argparse.ArgumentParser(description="State Procurement Data Scraper")
    parser.add_argument("--state", type=str, default="GA", help="Target state code (e.g. GA, AL)")
    parser.add_argument("--output", type=str, default=None, help="Output JSON path")
    parser.add_argument("--max-pages", type=int, default=1, help="Max search result pages to scrape")
    parser.add_argument("--headed", action="store_true", help="Run browser in headed mode")
    
    args = parser.parse_args()
    
    state_code = args.state.upper()
    if args.output:
        output_path = Path(args.output)
    else:
        output_path = Path(f"data/output/{state_code.lower()}_bids.json")
    
    asyncio.run(
        run_scraper_pipeline(
            state=state_code,
            output_path=output_path,
            max_pages=args.max_pages,
            headless=not args.headed,
        )
    )

if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run E2E tests to verify they pass**

Run: `.venv/bin/pytest tests/test_e2e.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add run_scraper.py tests/test_e2e.py
git commit -m "feat: integrate Alabama state support into CLI and test pipeline"
```

---

### Task 3: Execute Scraper Pipeline for Alabama and Verify Output

- [ ] **Step 1: Execute `run_scraper.py --state AL`**

Run: `.venv/bin/python run_scraper.py --state AL --max-pages 1`
Expected: Outputs "Successfully scraped and saved N bids to data/output/alabama_bids.json"

- [ ] **Step 2: Run full test suite**

Run: `.venv/bin/pytest tests/ -v`
Expected: All tests PASS.

- [ ] **Step 3: Commit output artifact**

```bash
git add data/output/alabama_bids.json
git commit -m "data: add initial scraped procurement output for Alabama"
```
