import json
import re
import urllib.parse
from datetime import datetime
from typing import List, Optional

from bs4 import BeautifulSoup
from crawl4ai import AsyncWebCrawler

from src.crawler import fetch_page, random_delay
from src.storage import BidEvent, BidContact, CommodityCode, Attachment

# JsonCssExtractionStrategy schema for the static, structurally stable parts
# of a Georgia Procurement Registry (GPR) event detail page: the commodity
# code table and the attachments table. Both are plain <table> rows with a
# fixed column layout, so a CSS schema captures them exactly like the old
# Playwright locator loop did. The label/value rows above them (Title,
# Agency, Status, ...) are NOT expressed as a schema because their row order
# and labels are not fixed - the old code found them by regex-matching the
# row's text - so that logic is preserved as-is over the fetched HTML
# (still fully deterministic, no LLM involved).
DETAIL_TABLES_SCHEMA = {
    "name": "georgia_event_detail_tables",
    "baseSelector": "body",
    "fields": [
        {
            "name": "commodity_rows",
            "selector": "table#commodity-codes tr, table[id*='commodity'] tr",
            "type": "list",
            "fields": [
                {"name": "code", "selector": "td:nth-of-type(1)", "type": "text"},
                {"name": "description", "selector": "td:nth-of-type(2)", "type": "text"},
            ],
        },
        {
            "name": "attachment_rows",
            "selector": "table#attachments tr, table[id*='attachment'] tr",
            "type": "list",
            "fields": [
                {"name": "first_cell", "selector": "td:nth-of-type(1)", "type": "text"},
                {"name": "link", "selector": "a", "type": "attribute", "attribute": "href"},
                {"name": "link_text", "selector": "a", "type": "text"},
            ],
        },
    ],
}

LISTING_LINKS_SCHEMA = {
    "name": "georgia_listing_links",
    "baseSelector": "a[href*='eventDetails']",
    "fields": [
        {"name": "href", "type": "attribute", "attribute": "href"},
    ],
}


class GeorgiaScraper:
    BASE_URL = "https://ssl.doas.state.ga.us/gpr/"

    async def scrape_all_bids(self, crawler: AsyncWebCrawler, max_pages: Optional[int] = 1) -> List[BidEvent]:
        session_id = "georgia-listing-session"
        results: List[BidEvent] = []
        current_page = 1

        # Initial navigation. Some GPR flows require clicking a "Search"
        # button on the homepage before the results list is populated - do
        # that as part of the first fetch via js_code, mirroring the old
        # Playwright click.
        search_js = (
            "const btn = document.querySelector(\"input[type='submit'][value*='Search']\") "
            "|| Array.from(document.querySelectorAll('button')).find(b => /search/i.test(b.textContent)); "
            "if (btn) { btn.click(); }"
        )
        result = await fetch_page(
            crawler,
            self.BASE_URL,
            js_code=search_js,
            session_id=session_id,
        )

        while True:
            html = result.html or ""
            soup = BeautifulSoup(html, "lxml") if html else None
            event_urls: List[str] = []
            if soup:
                for link in soup.select("a[href*='eventDetails']"):
                    href = link.get("href")
                    if href:
                        full_url = urllib.parse.urljoin(self.BASE_URL, href)
                        if full_url not in event_urls:
                            event_urls.append(full_url)

            if not event_urls:
                break

            for url in event_urls:
                detail_bid = await self._scrape_detail_page(crawler, url)
                if detail_bid:
                    results.append(detail_bid)

            if max_pages and current_page >= max_pages:
                break

            # Pagination: reuse the same session and click "Next" via JS
            # (Playwright-only pseudo-selectors like :has-text aren't valid
            # browser CSS, so we match by textContent in plain JS instead).
            next_js = (
                "const cands = Array.from(document.querySelectorAll('a, button'));"
                "const btn = cands.find(el => /next|>/i.test((el.textContent || '').trim()));"
                "if (btn && !btn.disabled) { btn.click(); window.__gprNextClicked = true; } "
                "else { window.__gprNextClicked = false; }"
            )
            await random_delay(1.0, 2.0)
            next_result = await fetch_page(
                crawler,
                self.BASE_URL,
                js_code=next_js,
                session_id=session_id,
                js_only=True,
            )
            if not next_result or not next_result.html:
                break
            # If nothing changed (no Next control), stop to avoid infinite loop.
            if next_result.html == html:
                break
            current_page += 1
            result = next_result

        return results

    async def _scrape_detail_page(self, crawler: AsyncWebCrawler, url: str) -> Optional[BidEvent]:
        try:
            result = await fetch_page(crawler, url, schema=DETAIL_TABLES_SCHEMA)
            await random_delay(0.3, 0.8)

            html = result.html or ""
            content_text = html

            # Parse event ID from URL or page text
            parsed_url = urllib.parse.urlparse(url)
            query_params = urllib.parse.parse_qs(parsed_url.query)
            event_id = query_params.get("eventId", [None])[0]

            if not event_id:
                eid_match = re.search(r'Event\s*ID[:\s]*([A-Za-z0-9_-]+)', content_text, re.IGNORECASE)
                event_id = eid_match.group(1) if eid_match else f"GA-{hash(url) & 0xffffffff}"

            now_str = datetime.now().isoformat()

            soup = BeautifulSoup(html, "lxml") if html else BeautifulSoup("", "lxml")

            def get_label_value(label_regex: str) -> Optional[str]:
                for row in soup.find_all("tr"):
                    row_text = row.get_text(" ", strip=True)
                    if re.search(label_regex, row_text, re.IGNORECASE):
                        cells = row.find_all(["td", "th"])
                        if len(cells) >= 2:
                            val = cells[1].get_text(strip=True)
                            if val:
                                return val
                        elif len(cells) == 1:
                            parts = row_text.split(":", 1)
                            if len(parts) > 1 and parts[1].strip():
                                return parts[1].strip()
                return None

            title = get_label_value(r'Title|Event\s*Name')
            if not title:
                for h in soup.find_all(["h1", "h2", "h3"]):
                    txt = h.get_text(strip=True)
                    if txt and "Event Details" not in txt:
                        title = txt
                        break
            if not title:
                title = "Georgia Procurement Event"

            agency = get_label_value(r'Agency|Department|Issuing\s*Entity') or "State of Georgia"
            solicitation_type = get_label_value(r'Solicitation\s*Type|Event\s*Type|Type') or "Solicitation"
            status = get_label_value(r'Status') or "Open"
            post_date = get_label_value(r'Post\s*Date|Issue\s*Date|Start\s*Date') or now_str[:10]
            close_date = get_label_value(r'Close\s*Date|End\s*Date|Due\s*Date') or now_str

            contact_name = get_label_value(r'Contact\s*Name|Contact')
            if contact_name and "Contact:" in contact_name:
                contact_name = contact_name.replace("Contact:", "").strip()

            if not contact_name:
                contact_match = re.search(r'Contact:\s*([^\n<]+)', content_text)
                if contact_match:
                    contact_name = contact_match.group(1).strip()

            contact_email = None
            email_match = re.search(r'[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}', content_text)
            if email_match:
                contact_email = email_match.group(0)

            contact_phone = None
            phone_match = re.search(r'\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}', content_text)
            if phone_match:
                contact_phone = phone_match.group(0)

            contact = None
            if contact_name or contact_email or contact_phone:
                contact = BidContact(name=contact_name, email=contact_email, phone=contact_phone)

            # Structured extraction (commodity codes + attachments) via the
            # JsonCssExtractionStrategy schema result when available; fall
            # back to scanning the soup directly if the schema found nothing
            # (e.g. tables without the expected id attributes).
            commodity_codes: List[CommodityCode] = []
            attachments: List[Attachment] = []
            extracted = _parse_extracted(result.extracted_content)
            if extracted:
                for row in extracted.get("commodity_rows", []) or []:
                    c_code = (row.get("code") or "").strip()
                    c_desc = (row.get("description") or "").strip()
                    if c_code and c_desc and re.match(r'^\d{3,5}$', c_code):
                        commodity_codes.append(CommodityCode(code=c_code, description=c_desc))

                for row in extracted.get("attachment_rows", []) or []:
                    href = row.get("link")
                    if not href:
                        continue
                    first_cell = (row.get("first_cell") or "").strip()
                    file_name = None
                    if first_cell and first_cell.lower() != "download":
                        file_name = first_cell
                    if not file_name:
                        file_name = (row.get("link_text") or "").strip() or "attachment"
                    full_download_url = urllib.parse.urljoin(self.BASE_URL, href)
                    file_type = file_name.split(".")[-1] if "." in file_name else None
                    attachments.append(Attachment(file_name=file_name, download_url=full_download_url, file_type=file_type))

            if not commodity_codes:
                for row in soup.find_all("tr"):
                    cells = row.find_all("td")
                    if len(cells) >= 2:
                        c_code = cells[0].get_text(strip=True)
                        c_desc = cells[1].get_text(strip=True)
                        if re.match(r'^\d{3,5}$', c_code) and c_desc:
                            commodity_codes.append(CommodityCode(code=c_code, description=c_desc))

            if not attachments:
                for a_link in soup.select(
                    "a[href*='download'], a[href*='file'], a[href*='.pdf'], a[href*='.doc'], table#attachments a"
                ):
                    href = a_link.get("href")
                    link_text = a_link.get_text(strip=True)
                    parent_row = a_link.find_parent("tr")
                    row_cells = parent_row.find_all("td") if parent_row else []
                    file_name = None
                    if row_cells:
                        cell_text = row_cells[0].get_text(strip=True)
                        if cell_text and cell_text.lower() != "download":
                            file_name = cell_text
                    if not file_name:
                        file_name = link_text or "attachment"
                    if href:
                        full_download_url = urllib.parse.urljoin(self.BASE_URL, href)
                        file_type = file_name.split(".")[-1] if "." in file_name else None
                        attachments.append(Attachment(file_name=file_name, download_url=full_download_url, file_type=file_type))

            return BidEvent(
                event_id=str(event_id),
                state="GA",
                portal_name="Georgia Procurement Registry",
                title=title,
                agency=agency,
                solicitation_type=solicitation_type,
                status=status,
                post_date=post_date,
                close_date=close_date,
                detail_url=url,
                contact=contact,
                commodity_codes=commodity_codes,
                attachments=attachments,
                scraped_at=now_str,
                updated_at=now_str,
            )
        except Exception as e:
            print(f"Error scraping detail page {url}: {e}")
            return None


def _parse_extracted(extracted_content: Optional[str]):
    """JsonCssExtractionStrategy returns a JSON string containing a LIST with
    one object per baseSelector match (here baseSelector is 'body', so it's a
    single-element list). Normalize to a plain dict or None."""
    if not extracted_content:
        return None
    try:
        data = json.loads(extracted_content)
    except (ValueError, TypeError):
        return None
    if isinstance(data, list):
        return data[0] if data else None
    return data
