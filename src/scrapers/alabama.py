import hashlib
import json
import re
import urllib.parse
from datetime import datetime, timezone
from typing import List, Optional

from bs4 import BeautifulSoup
from crawl4ai import AsyncWebCrawler

from src.crawler import fetch_page, random_delay
from src.storage import (
    CrawlInfo,
    DeliveryDocument,
    DeliveryOpportunity,
    DeliveryRecord,
    DeliverySource,
    RawCapture,
)

PARSER_VERSION = "alabama-v1.1.0"

# JsonCssExtractionStrategy schema for the document/attachment links on a
# BidNet Direct detail page - a static, structurally stable pattern (any
# <a> whose href looks like a PDF/attachment/download link). The label/value
# fields above (Publication Date, Closing Date, Location, Issuing Agency)
# are NOT expressed as a schema because BidNet's layout puts them in
# free-flowing text blocks, not a fixed table - the old code found them by
# regex-matching the page body text, so that logic is preserved as-is over
# the fetched HTML (still fully deterministic, no LLM involved).
DOCUMENT_LINKS_SCHEMA = {
    "name": "alabama_detail_documents",
    "baseSelector": "a[href*='.pdf'], a[href*='attachment'], a[href*='download']",
    "fields": [
        {"name": "href", "type": "attribute", "attribute": "href"},
        {"name": "text", "type": "text"},
    ],
}


class AlabamaScraper:
    BASE_URL = "https://www.bidnetdirect.com/alabama"

    def _extract_event_id(self, url: str) -> str:
        match = re.search(r'/(\d+)(?:\?|$|\/)', url)
        if match:
            return match.group(1)
        return f"AL-{hash(url) & 0xffffffff}"

    async def scrape_all_bids(
        self, crawler: AsyncWebCrawler, max_pages: Optional[int] = 1, run_id: str = "adhoc-run"
    ) -> List[DeliveryRecord]:
        session_id = "alabama-listing-session"
        results: List[DeliveryRecord] = []
        current_page = 1

        result = await fetch_page(crawler, self.BASE_URL, session_id=session_id)
        await random_delay(0.5, 1.5)

        while True:
            html = result.html or ""
            soup = BeautifulSoup(html, "lxml") if html else None

            bids_to_scrape = []
            seen_urls = set()
            if soup:
                for link in soup.find_all("a", href=True):
                    href = link.get("href")
                    if href and re.search(r'/\d{5,}', href):
                        full_url = urllib.parse.urljoin(self.BASE_URL, href)
                        if full_url not in seen_urls:
                            seen_urls.add(full_url)
                            summary_title = link.get_text(strip=True)
                            bids_to_scrape.append((full_url, summary_title))

            if not bids_to_scrape:
                break

            for url, summary_title in bids_to_scrape:
                detail_bid = await self._scrape_detail_page(crawler, url, summary_title, run_id)
                if detail_bid:
                    results.append(detail_bid)

            if max_pages and current_page >= max_pages:
                break

            next_js = (
                "const cands = Array.from(document.querySelectorAll('a, button'));"
                "const btn = cands.find(el => /next|>/i.test((el.textContent || '').trim()));"
                "if (btn && !btn.disabled) { btn.click(); }"
            )
            await random_delay(0.5, 1.5)
            next_result = await fetch_page(
                crawler, self.BASE_URL, js_code=next_js, session_id=session_id, js_only=True
            )
            if not next_result or not next_result.html or next_result.html == html:
                break
            current_page += 1
            result = next_result

        return results

    async def _scrape_detail_page(
        self, crawler: AsyncWebCrawler, url: str, summary_title: str = "", run_id: str = "adhoc-run"
    ) -> Optional[DeliveryRecord]:
        try:
            result = await fetch_page(crawler, url, schema=DOCUMENT_LINKS_SCHEMA)
            await random_delay(0.5, 1.0)

            event_id = self._extract_event_id(url)
            now_str = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

            raw_html = result.html or ""
            raw_sha256 = hashlib.sha256(raw_html.encode("utf-8")).hexdigest()

            soup = BeautifulSoup(raw_html, "lxml") if raw_html else BeautifulSoup("", "lxml")
            body_text = soup.get_text("\n", strip=True)

            pub_match = re.search(r'Publication\s*Date[:\s]*\n+\s*([^\n]+)', body_text, re.IGNORECASE) or re.search(r'Publication\s*Date[:\s]*([^\n<]+)', body_text, re.IGNORECASE)
            close_match = re.search(r'Closing\s*Date[:\s]*\n+\s*([^\n]+)', body_text, re.IGNORECASE) or re.search(r'Closing\s*Date[:\s]*([^\n<]+)', body_text, re.IGNORECASE)
            loc_match = re.search(r'Location[:\s]*\n+\s*([^\n]+)', body_text, re.IGNORECASE) or re.search(r'Location[:\s]*([^\n<]+)', body_text, re.IGNORECASE)
            agency_match = re.search(r'Issuing\s*(?:Agency|Organization)[:\s]*\n*\s*([^\n<]+)', body_text, re.IGNORECASE)

            post_date = pub_match.group(1).strip() if pub_match else now_str[:10]
            close_date = close_match.group(1).strip() if close_match else now_str
            location = loc_match.group(1).strip() if loc_match else "Alabama"

            title = summary_title
            title_elem = soup.find(["h1", "h2"])
            if title_elem:
                txt = title_elem.get_text(strip=True)
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
            errors: List[str] = []
            seen_doc_urls = set()
            for row in _parse_extracted(result.extracted_content) or []:
                href = row.get("href")
                if not href:
                    continue
                doc_url = urllib.parse.urljoin(url, href)
                if doc_url in seen_doc_urls:
                    continue
                seen_doc_urls.add(doc_url)
                doc_title = (row.get("text") or "").strip() or None
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


def _parse_extracted(extracted_content: Optional[str]):
    """JsonCssExtractionStrategy returns a JSON string: a list with one dict
    per match of the baseSelector (here, one dict per matched <a> tag)."""
    if not extracted_content:
        return None
    try:
        data = json.loads(extracted_content)
    except (ValueError, TypeError):
        return None
    if isinstance(data, list):
        return data
    return None
