# procurement_data_scraper
Scraping State Websites

## Setup

```bash
uv sync
uv run crawl4ai-setup   # one-time: installs the managed Chromium browser
```

Copy `.env.example` to `.env` (or export the variables directly) and fill in what you need:

- `GEMINI_API_KEY` — optional. Enables Gemini-based date/text formatting (`src/normalizer.py`) and the selector-free `--state DYNAMIC` extraction path (`src/dynamic_scraper.py`). Without it, both fall back to deterministic-only behavior.
- `DATABASE_URL` — optional. Additionally persists scraped records to Postgres (`src/db.py`), alongside the JSON/JSONL file output. See [Postgres setup](#postgres-setup) below.
- `OBJECT_STORAGE_DIR` — optional, defaults to `data/object_store`. Where raw page captures and downloaded documents are saved (`src/object_storage.py`); `raw_capture.storage_uri`/`documents[].storage_uri` in the delivered records point here as `file://` URIs.

Never commit `.env` — it's already in `.gitignore`.

## Running a scraper

```bash
uv run python run_scraper.py --state GA --output data/output/georgia_bids.json
uv run python run_scraper.py --state DYNAMIC --url "https://example.gov/bids" --portal-name "Example Portal"
```

## Postgres setup

The schema in `db/` covers only the "Source collection and retention" tables from `postgresql-data-dictionary.md` (`source_system`, `source_endpoint`, `crawl_run`, `source_record`, `source_record_version`, `raw_artifact`) — the tables this scraper project actually writes to. Everything past that section of the data dictionary (opportunity dedup, taxonomy/classification, business matching, awards, embeddings) is downstream processing owned by other teams per `scraper-team-minimum-spec (1).md`.

One-time setup:

```bash
sudo -u postgres psql -f db/bootstrap.sql   # edit the password in that file first
psql "$DATABASE_URL" -f db/schema.sql
```

Once `DATABASE_URL` is set, every scrape run additionally writes to Postgres automatically — no extra flag needed. Leaving it unset skips Postgres entirely and only produces the JSON/JSONL file, unchanged from before this was added.

### Retention / purge

Raw captures and documents are saved with a `retained_until` three calendar months out (`postgresql-data-dictionary.md`'s retention rule). Nothing deletes them automatically on its own — run the purge job periodically (e.g. a daily cron):

```bash
uv run python purge_expired.py
```

This deletes the stored file for any `raw_artifact` row past its `retained_until` and marks that row `purge_status = 'purged'`. It does not delete `source_record`/`source_record_version` rows — per the spec, removing those is "the receiving team['s]... final removal process," not this scraper's job.

### robots.txt

`--state DYNAMIC` checks robots.txt (`src/robots.py`) before fetching a listing or detail page, and refuses to scrape a disallowed URL. The 6 selector-based scrapers (`src/scrapers/*.py`) don't carry this check — they target specific state portals that were manually reviewed and assigned, per the spec's "work only on the websites... assigned to you" rule, rather than arbitrary URLs a caller supplies.
# Python_State_scraping
