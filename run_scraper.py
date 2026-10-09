import argparse
import asyncio
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional

from dotenv import load_dotenv

load_dotenv()  # loads .env (GEMINI_API_KEY, DATABASE_URL, OBJECT_STORAGE_DIR) so
# scrapes persist to Postgres automatically without manually exporting env vars first.

from src.crawler import get_crawler
from src.scrapers.georgia import GeorgiaScraper
from src.scrapers.alabama import AlabamaScraper
from src.scrapers.jaggaer import MontanaJaggaerScraper, UtahJaggaerScraper, IowaJaggaerScraper, DelawareJaggaerScraper
from src.dynamic_scraper import RobotsDisallowedError, scrape_dynamic
from src.storage import DeliveryRecord, StorageManager


def _maybe_persist_to_postgres(
    records: List[DeliveryRecord],
    *,
    source_name: str,
    base_url: str,
    endpoint_url: str,
    scraper_version: str,
) -> None:
    """Additionally write records to Postgres (see src/db.py) when
    DATABASE_URL is set. This is an extra delivery path alongside the JSON
    file output above, not a replacement - if DATABASE_URL is unset, this
    is a no-op and the JSON file remains the only output, unchanged from
    before this was added."""
    if not os.environ.get("DATABASE_URL") or not records:
        return
    from src.db import persist_delivery_records, get_connection

    conn = get_connection()
    try:
        crawl_run_id = persist_delivery_records(
            conn,
            records,
            source_name=source_name,
            base_url=base_url,
            endpoint_url=endpoint_url,
            scraper_version=scraper_version,
        )
        print(f"Persisted {len(records)} record(s) to Postgres (crawl_run={crawl_run_id})")
    finally:
        conn.close()

async def run_scraper_pipeline(
    state: str,
    output_path: Path,
    max_pages: int = 1,
    headless: bool = True,
    *,
    dynamic_url: Optional[str] = None,
    dynamic_portal_name: Optional[str] = None,
) -> int:
    """
    Execute end-to-end procurement scraping pipeline for a given state.
    """
    state_upper = state.upper()

    if state_upper == "DYNAMIC":
        # No selector-based scraper for this portal yet: fetch via Crawl4AI
        # and extract structured listings via Gemini instead (see
        # src/dynamic_scraper.py for the cost/reliability tradeoffs this
        # implies vs. the selector-based scrapers below).
        if not dynamic_url:
            raise ValueError("--url is required when --state DYNAMIC is used")
        run_id = f"team-run-{datetime.now(timezone.utc).strftime('%Y-%m-%d-%H%M%S')}"
        portal_name = dynamic_portal_name or dynamic_url
        try:
            async with get_crawler(headless=headless) as crawler:
                records = await scrape_dynamic(
                    crawler,
                    dynamic_url,
                    state_code="XX",
                    portal_name=portal_name,
                    base_url=dynamic_url,
                    run_id=run_id,
                )
        except RobotsDisallowedError as e:
            print(f"Refusing to scrape: {e}")
            return 0
        storage = StorageManager()
        storage.append_delivery_records_jsonl(output_path, records)
        partial_count = sum(1 for r in records if r.crawl.record_status == "partial")
        print(
            f"Successfully scraped and appended {len(records)} dynamically-extracted "
            f"records to {output_path} ({partial_count} flagged partial)"
        )
        _maybe_persist_to_postgres(
            records,
            source_name=portal_name,
            base_url=dynamic_url,
            endpoint_url=dynamic_url,
            scraper_version="dynamic-gemini-v1",
        )
        return len(records)

    if state_upper not in ["GA", "AL", "MT", "UT", "IA", "DE"]:
        raise ValueError(f"Unsupported state: {state}")

    output_path = Path(output_path)
    print(f"Starting scraper for state: {state_upper}")
    run_id = f"team-run-{datetime.now(timezone.utc).strftime('%Y-%m-%d-%H%M%S')}"

    storage = StorageManager()

    if state_upper == "GA":
        # Georgia has been migrated to Crawl4AI (see src/crawler.py).
        async with get_crawler(headless=headless) as crawler:
            scraper = GeorgiaScraper()
            bids = await scraper.scrape_all_bids(crawler, max_pages=max_pages)
        storage.upsert_bids(output_path, bids)
        print(f"Successfully scraped and saved {len(bids)} bids to {output_path}")

        # GA still uses the legacy BidEvent model rather than DeliveryRecord,
        # so it needs its own conversion before it can flow into Postgres -
        # only do this (and the attachment downloads it involves) when
        # DATABASE_URL is actually set, to avoid pointless work otherwise.
        if os.environ.get("DATABASE_URL") and bids:
            from src.convert_to_delivery_spec import bidevent_to_delivery_record

            delivery_records = [
                await asyncio.to_thread(
                    bidevent_to_delivery_record, bid, run_id=run_id, parser_version="georgia-v1.0.0"
                )
                for bid in bids
            ]
            _maybe_persist_to_postgres(
                delivery_records,
                source_name="Georgia Procurement Registry",
                base_url="https://ssl.doas.state.ga.us/gpr/",
                endpoint_url="https://ssl.doas.state.ga.us/gpr/",
                scraper_version="georgia-v1.0.0",
            )
        return len(bids)

    if state_upper == "AL":
        # Alabama has been migrated to Crawl4AI (see src/crawler.py).
        async with get_crawler(headless=headless) as crawler:
            scraper = AlabamaScraper()
            records = await scraper.scrape_all_bids(crawler, max_pages=max_pages, run_id=run_id)
        storage.append_delivery_records_jsonl(output_path, records)
        print(f"Successfully scraped and appended {len(records)} spec-compliant records to {output_path}")
        _maybe_persist_to_postgres(
            records,
            source_name="Alabama BidNet Direct",
            base_url=scraper.BASE_URL,
            endpoint_url=scraper.BASE_URL,
            scraper_version="alabama-v1.1.0",
        )
        return len(records)

    # Remaining Jaggaer states have also been migrated to Crawl4AI.
    if state_upper in ("MT", "UT", "IA", "DE"):
        scraper_cls = {
            "MT": MontanaJaggaerScraper,
            "UT": UtahJaggaerScraper,
            "IA": IowaJaggaerScraper,
            "DE": DelawareJaggaerScraper,
        }[state_upper]
        async with get_crawler(headless=headless) as crawler:
            scraper = scraper_cls()
            records = await scraper.scrape_all_bids(crawler, max_pages=max_pages, run_id=run_id)
        storage.append_delivery_records_jsonl(output_path, records)
        print(f"Successfully scraped and appended {len(records)} spec-compliant records to {output_path}")
        _maybe_persist_to_postgres(
            records,
            source_name=scraper.portal_name,
            base_url=scraper.base_url,
            endpoint_url=scraper.base_url,
            scraper_version=getattr(scraper, "PARSER_VERSION", run_id),
        )
        return len(records)

def main():
    parser = argparse.ArgumentParser(description="State Procurement Data Scraper")
    parser.add_argument("--state", type=str, default="GA", help="Target state code (e.g. GA, AL), or DYNAMIC for a selector-free Gemini-extraction run")
    parser.add_argument("--output", type=str, default=None, help="Output JSON path")
    parser.add_argument("--max-pages", type=int, default=1, help="Max search result pages to scrape")
    parser.add_argument("--headed", action="store_true", help="Run browser in headed mode")
    parser.add_argument("--url", type=str, default=None, help="Listing page URL to scrape (required with --state DYNAMIC)")
    parser.add_argument("--portal-name", type=str, default=None, help="Human-readable portal name to record (used with --state DYNAMIC)")

    args = parser.parse_args()
    
    state_code = args.state.upper()
    state_names = {
        "GA": "georgia",
        "AL": "alabama",
        "MT": "montana",
        "UT": "utah",
        "IA": "iowa",
        "DE": "delaware"
    }
    if args.output:
        output_path = Path(args.output)
    else:
        state_name = state_names.get(state_code, state_code.lower())
        # GA still uses the legacy BidEvent/JSON-array format (upsert_bids);
        # every other state delivers spec-compliant newline-delimited JSON.
        extension = "json" if state_code == "GA" else "jsonl"
        output_path = Path(f"data/output/{state_name}_bids.{extension}")
    
    asyncio.run(
        run_scraper_pipeline(
            state=state_code,
            output_path=output_path,
            max_pages=args.max_pages,
            headless=not args.headed,
            dynamic_url=args.url,
            dynamic_portal_name=args.portal_name,
        )
    )

if __name__ == "__main__":
    main()
