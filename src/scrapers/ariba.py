import re
import urllib.parse
from datetime import datetime
from typing import List, Optional

from bs4 import BeautifulSoup
from crawl4ai import AsyncWebCrawler

from src.crawler import fetch_page, random_delay
from src.storage import BidEvent, BidContact, CommodityCode, Attachment


def _soup_from_html(html: Optional[str]) -> BeautifulSoup:
    return BeautifulSoup(html or "", "lxml")


class AribaScraper:
    LIST_PATH = "/search/bids"

    def __init__(
        self,
        state_code: str,
        base_url: str,
        portal_name: str,
        search_entry_path: Optional[str] = None,
        agency_fallback: Optional[str] = None,
    ):
        self.state_code = state_code
        self.base_url = base_url.rstrip("/")
        self.portal_name = portal_name
        self.search_entry_path = search_entry_path or self.LIST_PATH
        self.agency_fallback = agency_fallback or f"State of {state_code}"
        self.LIST_URL = urllib.parse.urljoin(self.base_url + "/", self.search_entry_path.lstrip("/"))

    def _extract_event_id(self, url: str, fallback_text: Optional[str] = None) -> str:
        if fallback_text:
            match = re.search(r'([A-Za-z]{1,4}-?\d{3,})', fallback_text)
            if match:
                return match.group(1)
        match = re.search(r'/(\d+)(?:\?|$|/)', url)
        if match:
            return match.group(1)
        match = re.search(r'[?&]id=([A-Za-z0-9-]+)', url)
        if match:
            return match.group(1)
        return f"{self.state_code}-{hash(url) & 0xffffffff}"

    def _prepare_search_js(self) -> Optional[str]:
        """Hook for subclasses: JS to run (clicks, filter selection, etc.) on the
        listing page before the result links are collected, replacing the old
        Playwright `_prepare_search(page)` hook."""
        return None

    def _collect_result_links(self, soup: BeautifulSoup) -> List[str]:
        urls = []
        seen = set()
        for link in soup.find_all("a", href=True):
            href = link.get("href")
            if not href:
                continue
            full_url = urllib.parse.urljoin(self.base_url + "/", href)
            if full_url not in seen:
                seen.add(full_url)
                urls.append(full_url)
        return urls

    def _next_page_js(self) -> str:
        return (
            "const cands = Array.from(document.querySelectorAll('a, button'));"
            "const btn = cands.find(el => /next/i.test((el.textContent || '').trim()));"
            "if (btn && !btn.disabled) { btn.click(); }"
        )

    async def scrape_all_bids(self, crawler: AsyncWebCrawler, max_pages: Optional[int] = 1) -> List[BidEvent]:
        session_id = f"ariba-{self.state_code}-session"
        results: List[BidEvent] = []
        current_page = 1

        result = await fetch_page(
            crawler, self.LIST_URL, js_code=self._prepare_search_js(), session_id=session_id
        )
        await random_delay(1.0, 2.0)

        while True:
            html = result.html or ""
            soup = _soup_from_html(html)
            event_urls = self._collect_result_links(soup)
            if not event_urls:
                break

            for url in event_urls:
                detail_bid = await self._scrape_detail_page(crawler, url)
                if detail_bid:
                    results.append(detail_bid)

            if max_pages and current_page >= max_pages:
                break

            current_page += 1
            await random_delay(0.5, 1.5)
            next_result = await fetch_page(
                crawler, self.LIST_URL, js_code=self._next_page_js(), session_id=session_id, js_only=True
            )
            if not next_result or not next_result.html or next_result.html == html:
                break
            result = next_result

        return results

    def _get_label_value(self, soup: BeautifulSoup, label_regex: str) -> Optional[str]:
        try:
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
        except Exception:
            pass
        return None

    def _extract_detail_fields(self, soup: BeautifulSoup, url: str) -> dict:
        raise NotImplementedError

    async def _scrape_detail_page(self, crawler: AsyncWebCrawler, url: str) -> Optional[BidEvent]:
        try:
            result = await fetch_page(crawler, url)
            await random_delay(0.5, 1.5)

            now_str = datetime.now().isoformat()
            soup = _soup_from_html(result.html)
            fields = self._extract_detail_fields(soup, url)

            event_id = fields.get("event_id") or self._extract_event_id(url)
            title = fields.get("title") or f"{self.portal_name} Solicitation"
            agency = fields.get("agency") or self.agency_fallback
            solicitation_type = fields.get("solicitation_type") or "Solicitation"
            status = fields.get("status") or "Open"
            post_date = fields.get("post_date") or now_str[:10]
            close_date = fields.get("close_date") or now_str

            contact = fields.get("contact")
            commodity_codes = fields.get("commodity_codes") or []
            attachments = fields.get("attachments") or []

            return BidEvent(
                event_id=str(event_id),
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
            return None


class FloridaAribaScraper(AribaScraper):
    def __init__(self):
        super().__init__(
            state_code="FL",
            base_url="https://vendor.myfloridamarketplace.com",
            portal_name="MyFloridaMarketPlace Vendor Bid System (VBS)",
            search_entry_path="/search/bids",
            agency_fallback="State of Florida",
        )

    def _prepare_search_js(self) -> Optional[str]:
        # Replicates the old Playwright flow: expand the "Ad Status" filter,
        # check "OPEN", then submit the search form.
        return (
            "function clickByText(sel, text) {"
            "  const el = Array.from(document.querySelectorAll(sel))"
            "    .find(e => (e.textContent || '').trim().includes(text));"
            "  if (el) { el.click(); return true; } return false;"
            "}"
            "clickByText('*', 'Ad Status');"
            "clickByText('*', 'OPEN');"
            "clickByText('button', 'Search');"
        )

    def _collect_result_links(self, soup: BeautifulSoup) -> List[str]:
        urls = []
        seen = set()
        for row in soup.select("table tr"):
            link = row.find("a")
            if link is None:
                continue
            href = link.get("href")
            link_text = link.get_text(strip=True)
            if not re.match(r'^[A-Za-z]{1,4}-\d+$', link_text) and not href:
                continue
            if href:
                full_url = urllib.parse.urljoin(self.base_url + "/", href)
            else:
                ad_id_match = re.search(r'\d+', link_text)
                if not ad_id_match:
                    continue
                full_url = urllib.parse.urljoin(
                    self.base_url + "/", f"search/bids/detail/{ad_id_match.group(0)}"
                )
            if full_url not in seen:
                seen.add(full_url)
                urls.append(full_url)
        return urls

    def _next_page_js(self) -> str:
        return (
            "const btn = document.querySelector(\"button[aria-label='Next page']\");"
            "if (btn && !btn.disabled) { btn.click(); }"
        )

    def _extract_detail_fields(self, soup: BeautifulSoup, url: str) -> dict:
        body_text = soup.get_text("\n", strip=True)

        event_id = None
        ad_number_match = re.search(r'Advertisement\s*Number:\s*([A-Za-z0-9-]+)', body_text, re.IGNORECASE)
        if ad_number_match:
            event_id = ad_number_match.group(1).strip()

        title = None
        h1 = soup.find("h1")
        if h1 is not None:
            txt = h1.get_text(strip=True)
            if txt:
                title = txt

        agency = None
        h1_headline = soup.find("h1", class_="mat-headline")
        if h1_headline is not None:
            agency_span = h1_headline.find_next_sibling("span")
            if agency_span is not None:
                txt = agency_span.get_text(strip=True)
                if txt and "Last Edit" not in txt:
                    agency = txt

        solicitation_type = None
        type_span = soup.select_one("span.mat-headline.f-sm")
        if type_span is not None:
            txt = type_span.get_text(strip=True)
            if txt:
                solicitation_type = txt

        status_match = re.search(r'Advertisement\s*Status:\s*([A-Za-z]+)', body_text, re.IGNORECASE)
        status = status_match.group(1).strip() if status_match else None

        post_date_match = re.search(r'Published\s*Date/?Time:\s*([^\n]+)', body_text, re.IGNORECASE)
        post_date = post_date_match.group(1).strip() if post_date_match else None

        close_date_match = re.search(r'End\s*Date/?Time:\s*\xa0?\s*([^\n]+)', body_text, re.IGNORECASE)
        close_date = close_date_match.group(1).strip() if close_date_match else None

        contact_name = None
        contact_phone = None
        contact_email = None
        name_match = re.search(r'Name:\s*([^\n]+)', body_text)
        if name_match:
            contact_name = name_match.group(1).strip()
        phone_match = re.search(r'Phone:\s*([^\n]+)', body_text)
        if phone_match:
            contact_phone = phone_match.group(1).strip()
        email_match = re.search(r'[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}', body_text)
        if email_match:
            contact_email = email_match.group(0)

        contact = None
        if contact_name or contact_email or contact_phone:
            contact = BidContact(name=contact_name, email=contact_email, phone=contact_phone)

        commodity_codes: List[CommodityCode] = []
        try:
            for row in soup.select("mfmp-commodity-codes-list tr, table tr"):
                cells = row.find_all("td")
                if len(cells) >= 2:
                    c_code = cells[0].get_text(strip=True)
                    c_desc = cells[1].get_text(strip=True)
                    if re.match(r'^\d{4,10}$', c_code) and c_desc:
                        commodity_codes.append(CommodityCode(code=c_code, description=c_desc))
        except Exception:
            pass

        attachments: List[Attachment] = []
        try:
            for row in soup.select("table tr"):
                link = row.select_one("a.document-link, a[href*='download'], a[href*='.pdf'], a[href*='.doc']")
                if link is None:
                    continue
                file_name = link.get_text(strip=True)
                href = link.get("href")
                if not file_name:
                    continue
                download_url = urllib.parse.urljoin(self.base_url + "/", href) if href else url
                file_type = file_name.split(".")[-1] if "." in file_name else None
                attachments.append(
                    Attachment(file_name=file_name, download_url=download_url, file_type=file_type)
                )
        except Exception:
            pass

        return {
            "event_id": event_id,
            "title": title,
            "agency": agency,
            "solicitation_type": solicitation_type,
            "status": status,
            "post_date": post_date,
            "close_date": close_date,
            "contact": contact,
            "commodity_codes": commodity_codes,
            "attachments": attachments,
        }


# JS run before Crawl4AI snapshots a North Carolina eVP detail page. Some
# fields there are Angular/Dynamics form <input> elements whose current value
# lives only in the live DOM `.value` property (the old Playwright code read
# it with `input_value()`). A static HTML snapshot does not reflect JS-set
# `.value` back onto the `value` *attribute*, so before capture we copy each
# known field's live value into its `value` attribute (and a data-* mirror,
# for inputs whose `value` attribute is otherwise stripped by the app) so the
# deterministic BeautifulSoup parsing below can see it.
NC_SYNC_INPUT_VALUES_JS = """
(function() {
    const ids = ['evp_solicitationnbr', 'evp_department_name', 'evp_owneridname'];
    for (const id of ids) {
        const el = document.getElementById(id);
        if (el && 'value' in el) {
            el.setAttribute('value', el.value || '');
        }
    }
})();
"""


class NorthCarolinaAribaScraper(AribaScraper):
    def __init__(self):
        super().__init__(
            state_code="NC",
            base_url="https://evp.nc.gov",
            portal_name="NC eProcurement Electronic Vendor Portal (eVP)",
            search_entry_path="/solicitations/",
            agency_fallback="State of North Carolina",
        )

    def _collect_result_links(self, soup: BeautifulSoup) -> List[str]:
        urls = []
        seen = set()
        for link in soup.select("a.details-link, a[href*='/solicitations/details']"):
            href = link.get("href")
            if not href:
                continue
            full_url = urllib.parse.urljoin(self.base_url + "/", href)
            if full_url not in seen:
                seen.add(full_url)
                urls.append(full_url)
        return urls

    def _next_page_js(self) -> str:
        return (
            "const btn = document.querySelector('a.entity-pager-next-link');"
            "if (btn) { btn.click(); }"
        )

    async def _scrape_detail_page(self, crawler: AsyncWebCrawler, url: str) -> Optional[BidEvent]:
        try:
            result = await fetch_page(crawler, url, js_code=NC_SYNC_INPUT_VALUES_JS)
            await random_delay(0.5, 1.5)

            now_str = datetime.now().isoformat()
            soup = _soup_from_html(result.html)
            fields = self._extract_detail_fields(soup, url)

            event_id = fields.get("event_id") or self._extract_event_id(url)
            title = fields.get("title") or f"{self.portal_name} Solicitation"
            agency = fields.get("agency") or self.agency_fallback
            solicitation_type = fields.get("solicitation_type") or "Solicitation"
            status = fields.get("status") or "Open"
            post_date = fields.get("post_date") or now_str[:10]
            close_date = fields.get("close_date") or now_str

            return BidEvent(
                event_id=str(event_id),
                state=self.state_code,
                portal_name=self.portal_name,
                title=title,
                agency=agency,
                solicitation_type=solicitation_type,
                status=status,
                post_date=post_date,
                close_date=close_date,
                detail_url=url,
                contact=fields.get("contact"),
                commodity_codes=fields.get("commodity_codes") or [],
                attachments=fields.get("attachments") or [],
                scraped_at=now_str,
                updated_at=now_str
            )
        except Exception as e:
            print(f"Error scraping detail page {url}: {e}")
            return None

    def _field_input_value(self, soup: BeautifulSoup, field_id: str) -> Optional[str]:
        el = soup.find(id=field_id)
        if el is not None:
            val = el.get("value")
            if val and val.strip():
                return val.strip()
        return None

    def _extract_detail_fields(self, soup: BeautifulSoup, url: str) -> dict:
        event_id = self._field_input_value(soup, "evp_solicitationnbr")

        title = None
        h1 = soup.find("h1")
        if h1 is not None:
            txt = h1.get_text(strip=True)
            if txt:
                title = txt

        agency = self._field_input_value(soup, "evp_department_name") \
            or self._get_label_value(soup, r'Department')

        solicitation_type = self._get_label_value(soup, r'Solicitation\s*Type')
        status = self._get_label_value(soup, r'Status\s*Reason|Status')
        post_date = self._get_label_value(soup, r'Opening\s*Date')
        close_date = self._get_label_value(soup, r'Posted\s*Date')

        commodity_codes: List[CommodityCode] = []
        primary_commodity = self._get_label_value(soup, r'Primary\s*Commodity\s*Code')
        if primary_commodity:
            code_match = re.search(r'(\d{4,10})\s*$', primary_commodity)
            if code_match:
                desc = primary_commodity[: code_match.start()].strip(" -")
                commodity_codes.append(CommodityCode(code=code_match.group(1), description=desc or primary_commodity))

        try:
            for row in soup.select("#Subgrid_new_1 table tr, .subgrid table tr"):
                cells = row.find_all("td")
                if len(cells) >= 2:
                    c_code = cells[0].get_text(strip=True)
                    c_desc = cells[1].get_text(strip=True)
                    if re.match(r'^\d{4,10}$', c_code) and c_desc:
                        commodity_codes.append(CommodityCode(code=c_code, description=c_desc))
        except Exception:
            pass

        contact_name = None
        contact_email = None
        contact_phone = None
        special_instructions = self._get_label_value(soup, r'Special\s*Instructions')
        owner = self._field_input_value(soup, "evp_owneridname") or self._get_label_value(soup, r'Owner')
        if owner:
            contact_name = owner
        if special_instructions:
            email_match = re.search(r'[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}', special_instructions)
            if email_match:
                contact_email = email_match.group(0)
            phone_match = re.search(r'\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}', special_instructions)
            if phone_match:
                contact_phone = phone_match.group(0)

        contact = None
        if contact_name or contact_email or contact_phone:
            contact = BidContact(name=contact_name, email=contact_email, phone=contact_phone)

        attachments: List[Attachment] = []
        try:
            for a_link in soup.select(".entity-notes a[href*='/_entity/annotation']"):
                href = a_link.get("href")
                link_text = a_link.get_text(strip=True)
                if not href:
                    continue
                file_name = re.sub(r'\s*\([\d.]+\s*[A-Za-z]+\)\s*$', '', link_text).strip() or "attachment"
                download_url = urllib.parse.urljoin(self.base_url + "/", href)
                file_type = file_name.split(".")[-1] if "." in file_name else None
                attachments.append(
                    Attachment(file_name=file_name, download_url=download_url, file_type=file_type)
                )
        except Exception:
            pass

        return {
            "event_id": event_id,
            "title": title,
            "agency": agency,
            "solicitation_type": solicitation_type,
            "status": status,
            "post_date": post_date,
            "close_date": close_date,
            "contact": contact,
            "commodity_codes": commodity_codes,
            "attachments": attachments,
        }
