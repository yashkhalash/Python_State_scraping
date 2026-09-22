# Task 1 Brief: Create AlabamaScraper Module and Unit Tests

**Plan File:** `docs/superpowers/plans/2026-08-04-alabama-scraper.md`
**Task Number:** 1

## Requirements
Implement `AlabamaScraper` module in `src/scrapers/alabama.py` and unit tests in `tests/test_alabama_scraper.py`.

### Files to touch:
- Create `src/scrapers/alabama.py`
- Modify `src/scrapers/__init__.py`
- Create `tests/test_alabama_scraper.py`

### Specifications:
1. `AlabamaScraper` class:
   - `BASE_URL = "https://www.bidnetdirect.com/alabama"`
   - Method `_extract_event_id(self, url: str) -> str`: extracts numeric ID from URL (e.g., `/0000432654?` -> `"0000432654"`), or fallback hash if not found.
   - Async method `scrape_all_bids(self, context: BrowserContext, max_pages: Optional[int] = 1) -> List[BidEvent]`:
     - Navigates to `BASE_URL`.
     - Uses `random_delay` from `src.stealth`.
     - Parses solicitation links (`a[href*='/solicitations/open-bids/']`, `a.solicitation-link`).
     - Calls `_scrape_detail_page(context, url, summary_title)` for each unique bid link.
     - Respects `max_pages` parameter.
   - Async method `_scrape_detail_page(self, context: BrowserContext, url: str, summary_title: str) -> Optional[BidEvent]`:
     - Navigates to detail page.
     - Extracts fields into `BidEvent`: `event_id`, `state="AL"`, `portal_name="Alabama Purchasing Group (BidNet Direct)"`, `title`, `agency`, `solicitation_type="Solicitation"`, `status="Open"`, `post_date`, `close_date`, `detail_url=url`, `scraped_at`, `updated_at`.
     - Sets `contact=None`, `commodity_codes=[]`, `attachments=[]`.
2. Update `src/scrapers/__init__.py` to export `AlabamaScraper`.
3. Unit tests in `tests/test_alabama_scraper.py`:
   - Test `_extract_event_id` with standard URL containing numeric ID.
   - Test `_extract_event_id` fallback behavior.
   - Run tests via `.venv/bin/pytest tests/test_alabama_scraper.py -v`.

### Report File:
Write task report to `.superpowers/sdd/2026-08-04-alabama-scraper/task-1-report.md`.
