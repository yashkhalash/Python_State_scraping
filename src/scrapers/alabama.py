import hashlib
import re
import urllib.parse
from datetime import datetime, timezone
from typing import List, Optional
from playwright.async_api import BrowserContext
from src.storage import (
    CrawlInfo,
    DeliveryDocument,
    DeliveryOpportunity,
    DeliveryRecord,
    DeliverySource,
    RawCapture,
)
from src.stealth import random_delay

PARSER_VERSION = "alabama-v1.1.0"

class AlabamaScraper:
    BASE_URL = "https://www.bidnetdirect.com/alabama"

    def _extract_event_id(self, url: str) -> str:
        match = re.search(r'/(\d+)(?:\?|$|\/)', url)
        if match:
            return match.group(1)
        return f"AL-{hash(url) & 0xffffffff}"

    async def scrape_all_bids(self, context: BrowserContext, max_pages: Optional[int] = 1, run_id: str = "adhoc-run") -> List[DeliveryRecord]:
        page = await context.new_page()
        try:
            await page.goto(self.BASE_URL, wait_until="domcontentloaded", timeout=30000)
            await page.wait_for_timeout(3000)
            await random_delay(0.5, 1.5)

            results: List[DeliveryRecord] = []
            current_page = 1

            while True:
                links = await page.locator("a").all()
                bids_to_scrape = []
                seen_urls = set()

                for link in links:
                    href = await link.get_attribute("href")
                    if href and re.search(r'/\d{5,}', href):
                        full_url = urllib.parse.urljoin("https://www.bidnetdirect.com", href)
                        if full_url not in seen_urls:
                            seen_urls.add(full_url)
                            summary_title = (await link.inner_text()).strip()
                            bids_to_scrape.append((full_url, summary_title))

                if not bids_to_scrape:
                    break

                for url, summary_title in bids_to_scrape:
                    detail_bid = await self._scrape_detail_page(context, url, summary_title, run_id)
                    if detail_bid:
                        results.append(detail_bid)

                if max_pages and current_page >= max_pages:
                    break

                next_btn = page.locator("a:has-text('Next'), button:has-text('Next'), a:has-text('>')").first
                if await next_btn.is_visible() and await next_btn.is_enabled():
                    current_page += 1
                    await next_btn.click()
                    await page.wait_for_load_state("domcontentloaded")
                    await page.wait_for_timeout(3000)
                    await random_delay(0.5, 1.5)
                else:
                    break

            return results
        finally:
            await page.close()

    async def _scrape_detail_page(self, context: BrowserContext, url: str, summary_title: str = "", run_id: str = "adhoc-run") -> Optional[DeliveryRecord]:
        detail_page = await context.new_page()
        errors: List[str] = []
        try:
            await detail_page.goto(url, wait_until="domcontentloaded", timeout=30000)
            await detail_page.wait_for_timeout(1000)
            await random_delay(0.5, 1.0)

            event_id = self._extract_event_id(url)
            now_str = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

            raw_html = await detail_page.content()
            raw_sha256 = hashlib.sha256(raw_html.encode("utf-8")).hexdigest()

            body_text = await detail_page.locator("body").inner_text()

            # Clean body text extraction
            pub_match = re.search(r'Publication\s*Date[:\s]*\n+\s*([^\n]+)', body_text, re.IGNORECASE) or re.search(r'Publication\s*Date[:\s]*([^\n<]+)', body_text, re.IGNORECASE)
            close_match = re.search(r'Closing\s*Date[:\s]*\n+\s*([^\n]+)', body_text, re.IGNORECASE) or re.search(r'Closing\s*Date[:\s]*([^\n<]+)', body_text, re.IGNORECASE)
            loc_match = re.search(r'Location[:\s]*\n+\s*([^\n]+)', body_text, re.IGNORECASE) or re.search(r'Location[:\s]*([^\n<]+)', body_text, re.IGNORECASE)
            agency_match = re.search(r'Issuing\s*(?:Agency|Organization)[:\s]*\n*\s*([^\n<]+)', body_text, re.IGNORECASE)

            post_date = pub_match.group(1).strip() if pub_match else now_str[:10]
            close_date = close_match.group(1).strip() if close_match else now_str
            location = loc_match.group(1).strip() if loc_match else "Alabama"

            title = summary_title
            title_elem = detail_page.locator("h1, h2").first
            if await title_elem.count() > 0:
                txt = (await title_elem.inner_text()).strip()
                if txt and "Bid Information" not in txt and "skip" not in txt.lower():
                    title = txt
            if not title:
                title = summary_title or "Alabama Procurement Event"

            agency = None
            if agency_match and "locked" not in agency_match.group(1).lower() and "registered" not in agency_match.group(1).lower():
                agency = agency_match.group(1).strip()
            
            if not agency and location and location.lower() != "alabama":
                agency = location
            if not agency and "for " in title.lower():
                entity_match = re.search(r'for\s+([A-Za-z0-9\s]+?)(?:$|\n|\-|\()', title, re.IGNORECASE)
                if entity_match:
                    agency = entity_match.group(1).strip()
            if not agency:
                agency = "State of Alabama"

            documents: List[DeliveryDocument] = []
            doc_links = await detail_page.locator("a[href*='.pdf'], a[href*='attachment'], a[href*='download']").all()
            seen_doc_urls = set()
            for doc_link in doc_links:
                href = await doc_link.get_attribute("href")
                if not href:
                    continue
                doc_url = urllib.parse.urljoin(url, href)
                if doc_url in seen_doc_urls:
                    continue
                seen_doc_urls.add(doc_url)
                doc_title = (await doc_link.inner_text()).strip() or None
                documents.append(DeliveryDocument(
                    title=doc_title,
                    url=doc_url,
                    document_type="solicitation",
                    mime_type="application/pdf" if ".pdf" in doc_url.lower() else None,
                    download_status="link_only",
                    storage_uri=None,
                    sha256=None,
                ))
            if documents:
                errors.append("document download/hashing not yet implemented; URLs captured as link_only")

            errors.append("raw_capture.storage_uri not set; no approved object-storage location configured")

            return DeliveryRecord(
                observed_at=now_str,
                source=DeliverySource(
                    name="Alabama Purchasing Group (BidNet Direct)",
                    base_url=self.BASE_URL,
                    opportunity_url=url,
                    source_opportunity_id=event_id,
                    access_level="public",
                ),
                opportunity=DeliveryOpportunity(
                    title=title,
                    agency_name=agency,
                    solicitation_number=None,
                    status="Open",
                    notice_type="Solicitation",
                    published_at=post_date,
                    due_at=close_date,
                    source_timezone="America/Chicago",
                    description=None,
                    categories=[],
                    submission_url=url,
                    submission_method="external_portal",
                ),
                documents=documents,
                raw_capture=RawCapture(
                    content_type="text/html",
                    storage_uri=None,
                    sha256=raw_sha256,
                ),
                crawl=CrawlInfo(
                    run_id=run_id,
                    parser_version=PARSER_VERSION,
                    record_status="partial" if errors else "ok",
                    errors=errors,
                ),
            )
        except Exception as e:
            print(f"Error scraping detail page {url}: {e}")
            return None
        finally:
            await detail_page.close()
