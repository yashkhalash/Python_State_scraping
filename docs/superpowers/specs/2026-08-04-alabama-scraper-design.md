# Design Specification: Alabama Procurement Scraper

**Date**: 2026-08-04  
**Target State**: Alabama (AL)  
**Target Portal**: Alabama Purchasing Group (BidNet Direct)  
**Portal URL**: `https://www.bidnetdirect.com/alabama`

## Overview
This design details the implementation of a Playwright-based web scraper for active state and local procurement opportunities in Alabama hosted on BidNet Direct (`https://www.bidnetdirect.com/alabama`). 

The scraper retrieves publicly accessible solicitation metadata without requiring vendor authentication, transforms the raw details into standardized `BidEvent` models, and persists the findings via `StorageManager` to `data/output/alabama_bids.json`.

---

## 1. System Architecture & Components

```
                    ┌─────────────────────────┐
                    │      run_scraper.py     │
                    └────────────┬────────────┘
                                 │
                        (state == 'AL')
                                 │
                                 ▼
                    ┌─────────────────────────┐
                    │     AlabamaScraper      │
                    └────────────┬────────────┘
                                 │
               ┌─────────────────┴─────────────────┐
               ▼                                   ▼
  ┌─────────────────────────┐         ┌─────────────────────────┐
  │   Landing List Page     │         │   Bid Detail Page       │
  │  (mets-table-row parse) │         │ (date/metadata fallback)│
  └────────────┬────────────┘         └────────────┬────────────┘
               │                                   │
               └─────────────────┬─────────────────┘
                                 │
                                 ▼
                    ┌─────────────────────────┐
                    │      BidEvent Model     │
                    └────────────┬────────────┘
                                 │
                                 ▼
                    ┌─────────────────────────┐
                    │     StorageManager      │
                    │ (alabama_bids.json)     │
                    └─────────────────────────┘
```

### Key Modules:
- **`src/scrapers/alabama.py`**: Contains `AlabamaScraper`.
  - Methods:
    - `scrape_all_bids(context: BrowserContext, max_pages: Optional[int] = 1) -> List[BidEvent]`
    - `_scrape_detail_page(context: BrowserContext, url: str, summary_data: dict) -> Optional[BidEvent]`
- **`run_scraper.py`**: Update CLI entry point to support `--state AL` with state-specific routing and output defaults.
- **`tests/test_alabama_scraper.py`**: Unit test suite using mock HTML fixtures and Playwright page mocks.

---

## 2. Data Extraction Strategy

### List Page Extraction (`https://www.bidnetdirect.com/alabama`):
- Locate open solicitation rows: `TR.mets-table-row` or `DIV.sol-info-container`.
- Extract fields per row:
  - **Solicitation Title**: Link text in `DIV.sol-title` / `a.solicitation-link`.
  - **Detail Href**: Attribute `href` from `a.solicitation-link`.
  - **Event ID**: Numeric sequence parsed from Href query string or path segment `/([0-9]{6,10})\?`.
  - **Location / Issuing Entity**: Extracted from `DIV.sol-info-col` text.
  - **Publication Date**: Extracted from Published date snippet in row text.
  - **Closing Date**: Extracted from Closing date snippet in row text.

### Detail Page Extraction (`https://www.bidnetdirect.com/alabama/solicitations/open-bids/...`):
- Used to confirm and supplement extracted metadata:
  - Precise publication and closing timestamps.
  - Location verification (e.g. County vs City vs State).
  - Explicit confirmation of solicitation status ("Open").

---

## 3. Data Model Mapping (`BidEvent`)

| `BidEvent` Field | Type | Mapping / Fallback Strategy |
|---|---|---|
| `event_id` | `str` | Parsed numeric ID from URL (e.g. `0000432654`). Fallback to hash if unparseable. |
| `state` | `str` | Static `"AL"` |
| `portal_name` | `str` | Static `"Alabama Purchasing Group (BidNet Direct)"` |
| `title` | `str` | Solicitation title extracted from list or detail page header. |
| `agency` | `str` | Extracted location or issuing entity (e.g. `"Madison County"`, `"City of Selma"`). Defaults to `"State of Alabama"`. |
| `solicitation_type` | `str` | Static `"Solicitation"` or `"Open Bid"` |
| `status` | `str` | Static `"Open"` |
| `post_date` | `str` | Extracted publication date string (`MM/DD/YYYY`). |
| `close_date` | `str` | Extracted closing date string (`MM/DD/YYYY` or timestamp). |
| `detail_url` | `str` | Fully qualified URL on `bidnetdirect.com`. |
| `contact` | `Optional[BidContact]` | `None` (Public view without login locks contact details). |
| `commodity_codes` | `List[CommodityCode]` | `[]` (Public view without login locks commodity codes). |
| `attachments` | `List[Attachment]` | `[]` (Public view without login locks document links). |
| `scraped_at` | `str` | ISO 8601 current timestamp. |
| `updated_at` | `str` | ISO 8601 current timestamp. |

---

## 4. Error Handling & Resiliency
- **Stealth Integration**: Uses `src.stealth.create_stealth_context` to avoid automated browser blocking.
- **Random Delays**: Uses `random_delay(0.5, 1.5)` between detail page visits to mimic natural user browsing speed.
- **Detail Failure Fallback**: If an individual detail page fails to load, the scraper retains the summary metadata collected from the list row.
- **Pagination Support**: Parses next page navigation (`a:has-text('Next')`, `.pagination-next`) up to `max_pages`.

---

## 5. Testing Plan
- **Unit Tests (`tests/test_alabama_scraper.py`)**:
  - Test URL parsing & Event ID extraction logic.
  - Test row parsing on HTML snippets from BidNet Direct.
  - Test `BidEvent` conversion.
- **End-to-End Test (`tests/test_e2e.py`)**:
  - Verify pipeline execution for state `AL` produces valid non-empty output in `data/output/alabama_bids.json`.
