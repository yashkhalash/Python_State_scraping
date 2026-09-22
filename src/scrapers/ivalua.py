import json
import re
import urllib.parse
from datetime import datetime
from typing import List, Optional

from bs4 import BeautifulSoup
from crawl4ai import AsyncWebCrawler

from src.crawler import fetch_page, random_delay
from src.storage import BidEvent, BidContact, CommodityCode, Attachment

# JsonCssExtractionStrategy schema for the Ivalua public RFx grid rows.
# The grid is a stable HTML table (id/class contains "grid"), one <tr> per
# row, with cell 0 always the edit/manage icon column (not data) - same
# assumption the previous Playwright-locator loop made. We pull the raw
# per-cell text plus the row's first <a href> in one schema pass; the
# heuristic column-guessing in `_parse_grid_row` (which cell is the code,
# title, agency, status, dates) is preserved unchanged since Ivalua's public
# grids don't have stable per-column headers across state deployments.
GRID_ROWS_SCHEMA = {
    "name": "ivalua_grid_rows",
    "baseSelector": "table[id*='grid'] tbody tr, table.iv-grid-view tbody tr",
    "fields": [
        {"name": "row_html", "type": "html"},
        {"name": "link", "selector": "a[href]", "type": "attribute", "attribute": "href"},
    ],
}

# JsonCssExtractionStrategy schema for the static parts of a detail page:
# the commodity-code table and attachment-style links. The label/value rows
# above (Title, Agency, Status, dates, contact) are NOT expressed as a
# schema because - exactly as in the original Playwright version - they are
# located dynamically by regex-matching each table row's label text, which a
# static CSS schema cannot express; that logic is preserved as deterministic
# BeautifulSoup/regex parsing over the fetched HTML.
DETAIL_SCHEMA = {
    "name": "ivalua_detail_tables",
    "baseSelector": "body",
    "fields": [
        {
            "name": "commodity_rows",
            "selector": "table tr",
            "type": "list",
            "fields": [
                {"name": "code", "selector": "td:nth-of-type(1)", "type": "text"},
                {"name": "description", "selector": "td:nth-of-type(2)", "type": "text"},
            ],
        },
        {
            "name": "attachment_links",
            "selector": "a[href*='download'], a[href*='file'], a[href*='.pdf'], a[href*='.doc'], a[href*='attachment']",
            "type": "list",
            "fields": [
                {"name": "href", "type": "attribute", "attribute": "href"},
                {"name": "text", "type": "text"},
            ],
        },
    ],
}


def _parse_extracted(extracted_content: Optional[str]):
    if not extracted_content:
        return None
    try:
        data = json.loads(extracted_content)
    except (ValueError, TypeError):
        return None
    return data


class IvaluaScraper:
    BROWSE_PATH = "/page.aspx/en/rfp/request_browse_public"

    def __init__(self, state_code: str, base_url: str, portal_name: str):
        self.state_code = state_code
        self.base_url = base_url.rstrip("/")
        self.portal_name = portal_name
        self.BROWSE_URL = urllib.parse.urljoin(self.base_url + "/", self.BROWSE_PATH.lstrip("/"))

    def _extract_event_id(self, url: str) -> str:
        match = re.search(r'/(\d+)(?:\?|$|/)', url)
        if match:
            return match.group(1)
        return f"{self.state_code}-{hash(url) & 0xffffffff}"

    async def scrape_all_bids(self, crawler: AsyncWebCrawler, max_pages: Optional[int] = 1) -> List[BidEvent]:
        session_id = f"ivalua-{self.state_code}-session"
        results: List[BidEvent] = []
        current_page = 1

        result = await fetch_page(crawler, self.BROWSE_URL, schema=GRID_ROWS_SCHEMA, session_id=session_id)
        await random_delay(0.5, 1.5)

        while True:
            rows = _parse_extracted(result.extracted_content) or []
            row_data = []
            for row in rows:
                href = row.get("link")
                row_html = row.get("row_html") or ""
                if not href:
                    continue
                soup = BeautifulSoup(row_html, "lxml")
                cell_texts = [c.get_text(strip=True) for c in soup.find_all("td")]
                full_url = urllib.parse.urljoin(self.base_url + "/", href)
                row_data.append((full_url, cell_texts))

            if not row_data:
                break

            for url, cell_texts in row_data:
                bid = await self._scrape_detail_page(crawler, url, cell_texts)
                if bid:
                    results.append(bid)

            if max_pages and current_page >= max_pages:
                break

            next_js = (
                "const cands = Array.from(document.querySelectorAll(\"[aria-label*='Next page'], a, button\"));"
                "const btn = cands.find(el => /next/i.test((el.getAttribute('aria-label') || el.textContent || '')));"
                "if (btn && !btn.disabled) { btn.click(); }"
            )
            await random_delay(0.5, 1.5)
            next_result = await fetch_page(
                crawler,
                self.BROWSE_URL,
                schema=GRID_ROWS_SCHEMA,
                js_code=next_js,
                session_id=session_id,
                js_only=True,
            )
            if not next_result or next_result.extracted_content == result.extracted_content:
                break
            current_page += 1
            result = next_result

        return results

    def _parse_grid_row(self, cell_texts: List[str]) -> dict:
        code = None
        title = None
        agency = None
        status = None
        post_date = None
        close_date = None

        # The first cell is consistently the edit/manage icon column in Ivalua's
        # public grid (an "Edit"/tooltip link with no bid-relevant content), not data.
        data_cells = [t.strip() for t in cell_texts[1:] if t and t.strip()]

        dates = []
        remaining = []
        for text in data_cells:
            if re.match(r'^\d{1,2}/\d{1,2}/\d{2,4}', text):
                dates.append(text)
            elif re.match(r'^\d+[dhms].*\d+(s|min)?$', text, re.IGNORECASE) or re.match(r'^\d+d\s', text):
                continue
            else:
                remaining.append(text)

        if dates:
            post_date = dates[0]
            if len(dates) > 1:
                close_date = dates[-1]

        for text in remaining:
            if status is None and re.search(r'\b(open|closed|awarded|cancel+ed|draft|published)\b', text, re.IGNORECASE) and len(text) < 40:
                status = text

        remaining = [t for t in remaining if t != status]

        if code is None:
            for text in remaining:
                if re.match(r'^[A-Za-z]{0,6}[-_]?\d{3,}[A-Za-z0-9-]*$', text) and len(text) <= 30 and " " not in text:
                    code = text
                    break

        text_candidates = [t for t in remaining if t != code]
        if text_candidates:
            ranked = sorted(text_candidates, key=len, reverse=True)
            title = ranked[0]
            if len(ranked) > 1:
                agency = ranked[1]

        return {
            "code": code,
            "title": title,
            "agency": agency,
            "status": status,
            "post_date": post_date,
            "close_date": close_date,
        }

    async def _scrape_detail_page(
        self, crawler: AsyncWebCrawler, url: str, cell_texts: Optional[List[str]] = None
    ) -> Optional[BidEvent]:
        cell_texts = cell_texts or []
        grid_fields = self._parse_grid_row(cell_texts)
        try:
            result = await fetch_page(crawler, url, schema=DETAIL_SCHEMA)
            await random_delay(0.5, 1.0)

            event_id = self._extract_event_id(url)
            now_str = datetime.now().isoformat()

            html = result.html or ""
            if "/bas/browser_check" in (result.redirected_url or result.url or url) or "captcha" in html.lower():
                return self._build_bid_from_grid(url, event_id, grid_fields, now_str)

            soup = BeautifulSoup(html, "lxml") if html else BeautifulSoup("", "lxml")
            body_text = soup.get_text("\n", strip=True)

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

            title = get_label_value(r'Title|Label|Object|Description') or grid_fields.get("title")
            if not title:
                heading = soup.find(["h1", "h2", "h3"])
                if heading:
                    txt = heading.get_text(strip=True)
                    if txt:
                        title = txt
            if not title:
                title = f"{self.portal_name} Solicitation"

            agency = (
                get_label_value(r'Agency|Buyer|Department|Issuing\s*Entity|Organization')
                or grid_fields.get("agency")
                or f"State of {self.state_code}"
            )
            solicitation_type = get_label_value(r'Type|Process\s*Type|Rfx\s*Type') or "RFP"
            status = get_label_value(r'Status') or grid_fields.get("status") or "Open"
            post_date = (
                get_label_value(r'Publication\s*(?:begin\s*)?[Dd]ate|Post\s*Date|Begin\s*Date|Start\s*Date')
                or grid_fields.get("post_date")
                or now_str[:10]
            )
            close_date = (
                get_label_value(r'Publication\s*end\s*[Dd]ate|Close\s*Date|End\s*Date|Due\s*Date|Submission\s*[Dd]ate')
                or grid_fields.get("close_date")
                or now_str
            )

            contact_name = get_label_value(r'Contact\s*Name|Buyer\s*Name|Contact')
            if not contact_name:
                name_match = re.search(r'Contact(?:\s*Name)?\s*[:\-]\s*([^\n<]+)', body_text, re.IGNORECASE)
                if name_match:
                    contact_name = name_match.group(1).strip()
            contact_email = None
            email_match = re.search(r'[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}', body_text)
            if email_match:
                contact_email = email_match.group(0)
            contact_phone = None
            phone_match = re.search(r'\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}', body_text)
            if phone_match:
                contact_phone = phone_match.group(0)

            contact = None
            if contact_name or contact_email or contact_phone:
                contact = BidContact(name=contact_name, email=contact_email, phone=contact_phone)

            extracted = _parse_extracted(result.extracted_content)
            extracted = extracted[0] if isinstance(extracted, list) and extracted else extracted

            commodity_codes: List[CommodityCode] = []
            if extracted:
                for row in extracted.get("commodity_rows", []) or []:
                    c_code = (row.get("code") or "").strip()
                    c_desc = (row.get("description") or "").strip()
                    if c_code and c_desc and re.match(r'^\d{3,8}$', c_code):
                        commodity_codes.append(CommodityCode(code=c_code, description=c_desc))

            attachments: List[Attachment] = []
            if extracted:
                for row in extracted.get("attachment_links", []) or []:
                    href = row.get("href")
                    if not href:
                        continue
                    link_text = (row.get("text") or "").strip()
                    file_name = link_text or href.split("/")[-1] or "attachment"
                    full_download_url = urllib.parse.urljoin(self.base_url + "/", href)
                    file_type = file_name.split(".")[-1] if "." in file_name else None
                    attachments.append(
                        Attachment(file_name=file_name, download_url=full_download_url, file_type=file_type)
                    )

            return BidEvent(
                event_id=str(grid_fields.get("code") or event_id),
                state=self.state_code,
                portal_name=self.portal_name,
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
                updated_at=now_str
            )
        except Exception as e:
            print(f"Error scraping detail page {url}: {e}")
            if grid_fields.get("title"):
                return self._build_bid_from_grid(url, self._extract_event_id(url), grid_fields, datetime.now().isoformat())
            return None

    def _build_bid_from_grid(self, url: str, event_id: str, grid_fields: dict, now_str: str) -> Optional[BidEvent]:
        if not grid_fields.get("title"):
            return None
        return BidEvent(
            event_id=str(grid_fields.get("code") or event_id),
            state=self.state_code,
            portal_name=self.portal_name,
            title=grid_fields.get("title") or f"{self.portal_name} Solicitation",
            agency=grid_fields.get("agency") or f"State of {self.state_code}",
            solicitation_type="RFP",
            status=grid_fields.get("status") or "Open",
            post_date=grid_fields.get("post_date") or now_str[:10],
            close_date=grid_fields.get("close_date") or now_str,
            detail_url=url,
            contact=None,
            commodity_codes=[],
            attachments=[],
            scraped_at=now_str,
            updated_at=now_str
        )


class ArizonaIvaluaScraper(IvaluaScraper):
    def __init__(self):
        super().__init__(
            state_code="AZ",
            base_url="https://app.az.gov",
            portal_name="Arizona Procurement Portal (APP)",
        )


class MarylandIvaluaScraper(IvaluaScraper):
    def __init__(self):
        super().__init__(
            state_code="MD",
            base_url="https://emma.maryland.gov",
            portal_name="eMaryland Marketplace Advantage (eMMA)",
        )


class OhioIvaluaScraper(IvaluaScraper):
    def __init__(self):
        super().__init__(
            state_code="OH",
            base_url="https://ohiobuys.ohio.gov",
            portal_name="OhioBuys",
        )


class VermontIvaluaScraper(IvaluaScraper):
    def __init__(self):
        super().__init__(
            state_code="VT",
            base_url="https://vtbuys.vermont.gov",
            portal_name="VTBuys",
        )
