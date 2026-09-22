# Georgia Procurement Registry Web Scraper Design

## Overview
A resilient, daily incremental web scraper built using Python and Playwright to extract solicitation metadata, category/commodity codes, and document attachment links from the Georgia Procurement Registry (GPR) and store them in structured JSON format.

## Requirements & Scope
- **Target Portal:** Georgia Procurement Registry (DOAS / Team Georgia Marketplace) at `https://ssl.doas.state.ga.us/gpr/`
- **Execution Model:** Daily incremental sync CLI tool.
- **Extraction Depth:** Deep extraction (Overview metadata + NIGP/Commodity codes + Attachment links/metadata + Contact Info).
- **Stealth & Anti-Bot:** Customized Playwright Chromium context stripping automation flags, setting realistic browser headers/fingerprints, and implementing human-like request pacing.
- **Output Storage:** Local JSON persistence (`data/output/georgia_bids.json`) using Pydantic models for validation and atomic updates.

---

## System Architecture & Directory Structure

```
procurement_data_scraper/
├── data/
│   ├── State_Procurement_Grant_Bid_Sites.xlsx
│   └── output/
│       └── georgia_bids.json        # Scraped bid metadata store
├── docs/
│   └── superpowers/
│       └── specs/
│           └── 2026-08-04-georgia-procurement-scraper-design.md
├── src/
│   ├── __init__.py
│   ├── stealth.py                    # Stealth Playwright context manager & pacing
│   ├── scrapers/
│   │   ├── __init__.py
│   │   └── georgia.py               # Georgia DOAS GPR scraper logic
│   └── storage.py                   # JSON persistence manager & Pydantic schema
├── run_scraper.py                   # CLI entrypoint
├── pyproject.toml                   # Project dependencies (playwright, pydantic, etc.)
└── tests/
    └── test_georgia_scraper.py      # Unit & integrity tests
```

---

## Detailed Component Specifications

### 1. Stealth Context Module (`src/stealth.py`)
- **Browser Launch Flags:** Pass `--disable-blink-features=AutomationControlled` to Playwright Chromium.
- **Navigator Spoofing:** Inject JavaScript via `page.add_init_script()` to remove `navigator.webdriver` and set standard `navigator.plugins`, `navigator.languages`.
- **Browser Fingerprint:** 
  - Desktop User-Agent: Modern Chrome on macOS / Windows.
  - Viewport: `1920x1080` desktop geometry.
  - Locale: `en-US`, Timezone: `America/New_York`.
  - HTTP Headers: Realistic `Accept-Language`, `Sec-Ch-Ua`, `Sec-Fetch-Site`, `Sec-Fetch-Mode`.
- **Request Pacing:** Randomized jitter delay (`1.2`s to `3.5`s) between page navigations and interactions.

### 2. Georgia Scraper (`src/scrapers/georgia.py`)
- **Search Navigation:** Load `https://ssl.doas.state.ga.us/gpr/`, submit blank search to retrieve all open/active solicitations.
- **Pagination Loop:** Extract item rows across all result pages.
- **Detail Scraping:** For each solicitation:
  - Extract Event ID, Title, Issuing Agency, Type, Status, Post Date, Close Date, Detail URL.
  - Parse contact details (Name, Email, Phone).
  - Extract NIGP/Commodity codes and descriptions.
  - Extract attachment filenames and download URLs.

### 3. Data Storage & Schema (`src/storage.py`)
- **Pydantic Model Schema:**
  - `event_id` (str, primary key)
  - `state` (str, default "GA")
  - `portal_name` (str)
  - `title` (str)
  - `agency` (str)
  - `solicitation_type` (str)
  - `status` (str)
  - `post_date` (str)
  - `close_date` (str)
  - `detail_url` (str)
  - `contact` (dict: name, email, phone)
  - `commodity_codes` (list of dicts: code, description)
  - `attachments` (list of dicts: file_name, download_url, file_type)
  - `scraped_at` (iso format string)
  - `updated_at` (iso format string)
- **Persistence Logic:**
  - Loads existing JSON from `data/output/georgia_bids.json`.
  - Upserts records by `event_id` (preserving original `scraped_at`, updating `updated_at` and modified fields).
  - Writes to a temporary file (`georgia_bids.json.tmp`) and atomically renames to `georgia_bids.json`.

---

## Verification & Testing Plan

1. **Unit & Validation Tests:** Validate Pydantic schema serialization/deserialization and atomic JSON upsert operations in `tests/test_georgia_scraper.py`.
2. **Stealth Verification:** Verify browser context headers and fingerprint overrides before target requests.
3. **End-to-End CLI Verification:** Run `uv run python run_scraper.py --state GA` and confirm data extraction accuracy in `data/output/georgia_bids.json`.
