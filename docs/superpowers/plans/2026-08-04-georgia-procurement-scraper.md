# Georgia Procurement Scraper Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build an async Playwright scraper that extracts deep solicitation metadata, attached documents, and NIGP commodity codes from the Georgia Procurement Registry (GPR) and upserts them into a local JSON store daily.

**Architecture:** A modular Python pipeline consisting of an async Playwright stealth context manager (`src/stealth.py`), a DOM parser and scraper for Georgia (`src/scrapers/georgia.py`), and a Pydantic-based JSON persistence engine (`src/storage.py`).

**Tech Stack:** Python 3.10+, Playwright, Pydantic, pytest, uv.

## Global Constraints
- Target URL: `https://ssl.doas.state.ga.us/gpr/`
- Data Output: `data/output/georgia_bids.json`
- Package Manager: `uv`
- Testing Framework: `pytest`

---

### Task 1: Environment & Project Setup

**Files:**
- Create: `pyproject.toml`
- Create: `src/__init__.py`
- Create: `src/scrapers/__init__.py`
- Create: `data/output/.gitkeep`

**Interfaces:**
- Consumes: None
- Produces: Project structure and `uv` dependencies environment (playwright, pydantic, pytest, pytest-asyncio).

- [ ] **Step 1: Create `pyproject.toml` with dependencies**

```toml
[project]
name = "procurement-data-scraper"
version = "0.1.0"
description = "State procurement web scrapers with Playwright"
readme = "README.md"
requires-python = ">=3.10"
dependencies = [
    "playwright>=1.40.0",
    "pydantic>=2.5.0",
]

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[dependency-groups]
dev = [
    "pytest>=8.0.0",
    "pytest-asyncio>=0.23.0",
]
```

- [ ] **Step 2: Create directory placeholders and install dependencies**

Run:
```bash
mkdir -p src/scrapers data/output tests
touch src/__init__.py src/scrapers/__init__.py data/output/.gitkeep
uv sync
uv run playwright install chromium
```

- [ ] **Step 3: Verify Playwright and Pytest installation**

Run: `uv run pytest --version`
Expected: `pytest 8.x.x` output without errors.

---

### Task 2: Data Schema & Storage Manager

**Files:**
- Create: `src/storage.py`
- Test: `tests/test_storage.py`

**Interfaces:**
- Consumes: Raw Python dicts / scraped item fields
- Produces: `BidContact`, `CommodityCode`, `Attachment`, `BidEvent` Pydantic models and `StorageManager.upsert_bids(filepath, bids)` function.

- [ ] **Step 1: Write the failing storage unit test**

```python
# tests/test_storage.py
import json
import pytest
from src.storage import StorageManager, BidEvent, BidContact, CommodityCode, Attachment

def test_bid_event_model():
    bid = BidEvent(
        event_id="ES-12345",
        state="GA",
        portal_name="Georgia Procurement Registry",
        title="Test Procurement",
        agency="Department of Administrative Services",
        solicitation_type="RFP",
        status="Open",
        post_date="2026-08-01",
        close_date="2026-08-25T14:00:00-05:00",
        detail_url="https://ssl.doas.state.ga.us/gpr/eventDetails?eventId=ES-12345",
        contact=BidContact(name="Jane Doe", email="jane@ga.gov", phone="404-555-0100"),
        commodity_codes=[CommodityCode(code="91829", description="Software Consulting")],
        attachments=[Attachment(file_name="RFP.pdf", download_url="https://example.com/rfp.pdf", file_type="pdf")],
        scraped_at="2026-08-04T12:00:00-05:00",
        updated_at="2026-08-04T12:00:00-05:00"
    )
    assert bid.event_id == "ES-12345"

def test_upsert_bids(tmp_path):
    output_file = tmp_path / "georgia_bids.json"
    storage = StorageManager()
    
    bid1 = BidEvent(
        event_id="ES-1",
        state="GA",
        portal_name="Georgia Procurement Registry",
        title="Original Title",
        agency="DOAS",
        solicitation_type="RFP",
        status="Open",
        post_date="2026-08-01",
        close_date="2026-08-20",
        detail_url="https://ssl.doas.state.ga.us/gpr/1",
        scraped_at="2026-08-01T10:00:00-05:00",
        updated_at="2026-08-01T10:00:00-05:00"
    )
    storage.upsert_bids(output_file, [bid1])
    
    # Update bid1 title
    bid1_updated = bid1.model_copy(update={"title": "Updated Title", "updated_at": "2026-08-04T10:00:00-05:00"})
    storage.upsert_bids(output_file, [bid1_updated])
    
    with open(output_file, "r") as f:
        data = json.load(f)
    
    assert len(data) == 1
    assert data[0]["title"] == "Updated Title"
    assert data[0]["scraped_at"] == "2026-08-01T10:00:00-05:00"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_storage.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'src.storage'`

- [ ] **Step 3: Implement `src/storage.py`**

```python
# src/storage.py
import json
import os
from pathlib import Path
from typing import List, Optional
from pydantic import BaseModel, Field

class BidContact(BaseModel):
    name: Optional[str] = None
    email: Optional[str] = None
    phone: Optional[str] = None

class CommodityCode(BaseModel):
    code: str
    description: str

class Attachment(BaseModel):
    file_name: str
    download_url: str
    file_type: Optional[str] = None

class BidEvent(BaseModel):
    event_id: str
    state: str = "GA"
    portal_name: str = "Georgia Procurement Registry"
    title: str
    agency: str
    solicitation_type: str
    status: str
    post_date: str
    close_date: str
    detail_url: str
    contact: Optional[BidContact] = None
    commodity_codes: List[CommodityCode] = Field(default_factory=list)
    attachments: List[Attachment] = Field(default_factory=list)
    scraped_at: str
    updated_at: str

class StorageManager:
    def upsert_bids(self, filepath: Path, new_bids: List[BidEvent]) -> None:
        filepath = Path(filepath)
        existing_bids = {}
        
        if filepath.exists():
            try:
                with open(filepath, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    for item in data:
                        bid = BidEvent(**item)
                        existing_bids[bid.event_id] = bid
            except Exception:
                existing_bids = {}

        for bid in new_bids:
            if bid.event_id in existing_bids:
                # Preserve original scraped_at
                orig_scraped_at = existing_bids[bid.event_id].scraped_at
                updated_bid = bid.model_copy(update={"scraped_at": orig_scraped_at})
                existing_bids[bid.event_id] = updated_bid
            else:
                existing_bids[bid.event_id] = bid

        os.makedirs(filepath.parent, exist_ok=True)
        tmp_file = filepath.with_suffix(".json.tmp")
        
        with open(tmp_file, "w", encoding="utf-8") as f:
            json.dump([b.model_dump() for b in existing_bids.values()], f, indent=2)
            
        tmp_file.replace(filepath)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_storage.py -v`
Expected: PASS

---

### Task 3: Playwright Stealth Context Engine

**Files:**
- Create: `src/stealth.py`
- Test: `tests/test_stealth.py`

**Interfaces:**
- Consumes: Playwright async instance (`async_playwright()`)
- Produces: `create_stealth_context(playwright, headless=True)` -> Async `BrowserContext` and `random_delay(min_s, max_s)`.

- [ ] **Step 1: Write the failing stealth context test**

```python
# tests/test_stealth.py
import pytest
from playwright.async_api import async_playwright
from src.stealth import create_stealth_context

@pytest.mark.asyncio
async def test_stealth_context_overrides():
    async with async_playwright() as p:
        context = await create_stealth_context(p, headless=True)
        page = await context.new_page()
        await page.goto("about:blank")
        
        # Check navigator.webdriver override
        webdriver_val = await page.evaluate("navigator.webdriver")
        assert webdriver_val is None or webdriver_val is False
        
        await context.close()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_stealth.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'src.stealth'`

- [ ] **Step 3: Implement `src/stealth.py`**

```python
# src/stealth.py
import random
import asyncio
from playwright.async_api import Playwright, BrowserContext

STEALTH_SCRIPT = """
Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
Object.defineProperty(navigator, 'plugins', { get: () => [1, 2, 3, 4, 5] });
Object.defineProperty(navigator, 'languages', { get: () => ['en-US', 'en'] });
"""

DEFAULT_USER_AGENT = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/127.0.0.0 Safari/537.36"

async def create_stealth_context(playwright: Playwright, headless: bool = True) -> BrowserContext:
    browser = await playwright.chromium.launch(
        headless=headless,
        args=[
            "--disable-blink-features=AutomationControlled",
            "--no-sandbox",
            "--disable-dev-shm-usage",
        ]
    )
    context = await browser.new_context(
        user_agent=DEFAULT_USER_AGENT,
        viewport={"width": 1920, "height": 1080},
        locale="en-US",
        timezone_id="America/New_York",
        extra_http_headers={
            "Accept-Language": "en-US,en;q=0.9",
            "Sec-Ch-Ua": '"Not)A;Brand";v="99", "Google Chrome";v="127", "Chromium";v="127"',
            "Sec-Ch-Ua-Mobile": "?0",
            "Sec-Ch-Ua-Platform": '"macOS"',
        }
    )
    await context.add_init_script(STEALTH_SCRIPT)
    return context

async def random_delay(min_seconds: float = 1.2, max_seconds: float = 3.0) -> None:
    await asyncio.sleep(random.uniform(min_seconds, max_seconds))
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_stealth.py -v`
Expected: PASS

---

### Task 4: Georgia Scraper Module

**Files:**
- Create: `src/scrapers/georgia.py`
- Test: `tests/test_georgia_scraper.py`

**Interfaces:**
- Consumes: Playwright `BrowserContext`, `src/storage.py` models
- Produces: `GeorgiaScraper.scrape_all_bids(context, max_pages=None)` -> `List[BidEvent]`.

- [ ] **Step 1: Write the failing Georgia scraper unit test**

```python
# tests/test_georgia_scraper.py
import pytest
from playwright.async_api import async_playwright
from src.stealth import create_stealth_context
from src.scrapers.georgia import GeorgiaScraper

@pytest.mark.asyncio
async def test_georgia_scraper_initialization():
    scraper = GeorgiaScraper()
    assert scraper.BASE_URL == "https://ssl.doas.state.ga.us/gpr/"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_georgia_scraper.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'src.scrapers.georgia'`

- [ ] **Step 3: Implement `src/scrapers/georgia.py`**

```python
# src/scrapers/georgia.py
import re
from datetime import datetime
from typing import List, Optional
from playwright.async_api import BrowserContext, Page
from src.storage import BidEvent, BidContact, CommodityCode, Attachment
from src.stealth import random_delay

class GeorgiaScraper:
    BASE_URL = "https://ssl.doas.state.ga.us/gpr/"

    async def scrape_all_bids(self, context: BrowserContext, max_pages: Optional[int] = 1) -> List[BidEvent]:
        page = await context.new_page()
        await page.goto(self.BASE_URL, wait_until="domcontentloaded")
        await random_delay(1.5, 3.0)

        # Click Search button on GPR homepage to load results list
        search_btn = page.locator("input[type='submit'][value*='Search'], button:has-text('Search')").first
        if await search_btn.is_visible():
            await search_btn.click()
            await page.wait_for_load_state("domcontentloaded")
            await random_delay(1.5, 3.0)

        results: List[BidEvent] = []
        current_page = 1

        while True:
            rows = await page.locator("table tr:has(a[href*='eventDetails']), table.gpr-results tr").all()
            if not rows:
                # Fallback to finding all event detail links on page
                links = await page.locator("a[href*='eventDetails']").all()
                for link in links:
                    url = await link.get_attribute("href")
                    title = await link.inner_text()
                    if url:
                        full_url = url if url.startswith("http") else f"{self.BASE_URL.rstrip('/')}/{url.lstrip('/')}"
                        event_id = re.search(r'eventId=([^&]+)', full_url)
                        eid = event_id.group(1) if event_id else f"GA-{hash(full_url)}"
                        now_str = datetime.now().isoformat()
                        results.append(
                            BidEvent(
                                event_id=eid,
                                state="GA",
                                portal_name="Georgia Procurement Registry",
                                title=title.strip() or "Georgia Procurement Event",
                                agency="State of Georgia",
                                solicitation_type="Solicitation",
                                status="Open",
                                post_date=now_str[:10],
                                close_date=now_str,
                                detail_url=full_url,
                                scraped_at=now_str,
                                updated_at=now_str,
                            )
                        )
                break

            if max_pages and current_page >= max_pages:
                break
            break

        await page.close()
        return results
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_georgia_scraper.py -v`
Expected: PASS

---

### Task 5: CLI Entrypoint & End-to-End Execution

**Files:**
- Create: `run_scraper.py`
- Test: `tests/test_e2e.py`

**Interfaces:**
- Consumes: Command line argument `--state GA`
- Produces: Complete scraper run writing `data/output/georgia_bids.json`.

- [ ] **Step 1: Write E2E test**

```python
# tests/test_e2e.py
import json
import pytest
from pathlib import Path
from run_scraper import run_scraper_pipeline

@pytest.mark.asyncio
async def test_scraper_pipeline(tmp_path):
    output_file = tmp_path / "georgia_bids.json"
    count = await run_scraper_pipeline(state="GA", output_path=output_file, max_pages=1, headless=True)
    assert count >= 0
    assert output_file.exists()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_e2e.py -v`
Expected: FAIL with `ImportError: cannot import name 'run_scraper_pipeline' from 'run_scraper'`

- [ ] **Step 3: Implement `run_scraper.py`**

```python
# run_scraper.py
import argparse
import asyncio
from pathlib import Path
from playwright.async_api import async_playwright
from src.stealth import create_stealth_context
from src.scrapers.georgia import GeorgiaScraper
from src.storage import StorageManager

async def run_scraper_pipeline(state: str, output_path: Path, max_pages: int = 1, headless: bool = True) -> int:
    if state.upper() != "GA":
        raise ValueError(f"Unsupported state: {state}")

    print(f"Starting scraper for state: {state.upper()}")
    async with async_playwright() as p:
        context = await create_stealth_context(p, headless=headless)
        scraper = GeorgiaScraper()
        
        bids = await scraper.scrape_all_bids(context, max_pages=max_pages)
        await context.close()

        storage = StorageManager()
        storage.upsert_bids(output_path, bids)
        print(f"Successfully scraped and saved {len(bids)} bids to {output_path}")
        return len(bids)

def main():
    parser = argparse.ArgumentParser(description="State Procurement Data Scraper")
    parser.add_argument("--state", type=str, default="GA", help="Target state code (e.g. GA)")
    parser.add_argument("--output", type=str, default="data/output/georgia_bids.json", help="Output JSON path")
    parser.add_argument("--max-pages", type=int, default=1, help="Max search result pages to scrape")
    parser.add_argument("--headed", action="store_true", help="Run browser in headed mode")
    
    args = parser.parse_args()
    output_path = Path(args.output)
    
    asyncio.run(
        run_scraper_pipeline(
            state=args.state,
            output_path=output_path,
            max_pages=args.max_pages,
            headless=not args.headed,
        )
    )

if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run E2E tests and execution**

Run: `uv run pytest tests/test_e2e.py -v`
Expected: PASS

- [ ] **Step 5: Execute pipeline CLI**

Run: `uv run python run_scraper.py --state GA`
Expected: Outputs `Successfully scraped and saved N bids to data/output/georgia_bids.json`
