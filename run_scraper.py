import argparse
import asyncio
from datetime import datetime, timezone
from pathlib import Path
from playwright.async_api import async_playwright
from src.stealth import create_stealth_context
from src.crawler import get_crawler
from src.scrapers.georgia import GeorgiaScraper
from src.scrapers.alabama import AlabamaScraper
from src.scrapers.jaggaer import MontanaJaggaerScraper, UtahJaggaerScraper, IowaJaggaerScraper, DelawareJaggaerScraper
from src.storage import StorageManager

async def run_scraper_pipeline(state: str, output_path: Path, max_pages: int = 1, headless: bool = True) -> int:
    """
    Execute end-to-end procurement scraping pipeline for a given state.
    """
    state_upper = state.upper()
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
        return len(bids)

    # Remaining states are still on the legacy Playwright path pending
    # migration to Crawl4AI.
    async with async_playwright() as p:
        context = await create_stealth_context(p, headless=headless)

        if state_upper == "AL":
            scraper = AlabamaScraper()
            records = await scraper.scrape_all_bids(context, max_pages=max_pages, run_id=run_id)
            await context.close()
            storage.append_delivery_records(output_path, records)
            print(f"Successfully scraped and appended {len(records)} spec-compliant records to {output_path}")
            return len(records)
        elif state_upper == "MT":
            scraper = MontanaJaggaerScraper()
            records = await scraper.scrape_all_bids(context, max_pages=max_pages, run_id=run_id)
            await context.close()
            storage.append_delivery_records(output_path, records)
            print(f"Successfully scraped and appended {len(records)} spec-compliant records to {output_path}")
            return len(records)
        elif state_upper == "UT":
            scraper = UtahJaggaerScraper()
            records = await scraper.scrape_all_bids(context, max_pages=max_pages, run_id=run_id)
            await context.close()
            storage.append_delivery_records(output_path, records)
            print(f"Successfully scraped and appended {len(records)} spec-compliant records to {output_path}")
            return len(records)
        elif state_upper == "IA":
            scraper = IowaJaggaerScraper()
            records = await scraper.scrape_all_bids(context, max_pages=max_pages, run_id=run_id)
            await context.close()
            storage.append_delivery_records(output_path, records)
            print(f"Successfully scraped and appended {len(records)} spec-compliant records to {output_path}")
            return len(records)
        elif state_upper == "DE":
            scraper = DelawareJaggaerScraper()
            records = await scraper.scrape_all_bids(context, max_pages=max_pages, run_id=run_id)
            await context.close()
            storage.append_delivery_records(output_path, records)
            print(f"Successfully scraped and appended {len(records)} spec-compliant records to {output_path}")
            return len(records)

def main():
    parser = argparse.ArgumentParser(description="State Procurement Data Scraper")
    parser.add_argument("--state", type=str, default="GA", help="Target state code (e.g. GA, AL)")
    parser.add_argument("--output", type=str, default=None, help="Output JSON path")
    parser.add_argument("--max-pages", type=int, default=1, help="Max search result pages to scrape")
    parser.add_argument("--headed", action="store_true", help="Run browser in headed mode")
    
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
        output_path = Path(f"data/output/{state_name}_bids.json")
    
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
