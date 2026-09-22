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
    BidEvent,
    BidContact,
    CommodityCode,
    Attachment,
    CrawlInfo,
    DeliveryDocument,
    DeliveryOpportunity,
    DeliveryRecord,
    DeliverySource,
    RawCapture,
)

EMAIL_RE = re.compile(r'[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}')
PHONE_RE = re.compile(r'\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}')


class JaggaerLoginRequiredError(Exception):
    """Raised when a Jaggaer/SciQuest portal's public event router redirects to a supplier login wall."""
    pass


def _soup_from_html(html: Optional[str]) -> BeautifulSoup:
    return BeautifulSoup(html or "", "lxml")


def _parse_extracted(extracted_content: Optional[str]):
    if not extracted_content:
        return None
    try:
        return json.loads(extracted_content)
    except (ValueError, TypeError):
        return None


def get_row_label_value(soup: BeautifulSoup, label_regex: str) -> Optional[str]:
    """
    Extracts a field value from Bootstrap-style label/value rows, i.e.
    <div class="row"><div class="col-*"><label>Label</label></div>
    <div class="col-*">Value</div></div>, as used by Iowa's bidopportunities.iowa.gov
    detail page. Same matching logic as the previous Playwright-locator version,
    now applied to a BeautifulSoup tree built from the Crawl4AI-fetched HTML.
    """
    try:
        for row in soup.select("div.row"):
            label_el = row.find("label")
            if label_el is None:
                continue
            label_text = label_el.get_text(strip=True)
            if re.search(label_regex, label_text, re.IGNORECASE):
                value_divs = row.select("div[class*='col-']")
                if value_divs:
                    val = value_divs[-1].get_text(strip=True)
                    if val:
                        return val
    except Exception:
        pass
    return None


def get_label_value(soup: BeautifulSoup, label_regex: str) -> Optional[str]:
    try:
        for row in soup.find_all("tr"):
            row_text = row.get_text(" ", strip=True)
            if re.search(label_regex, row_text, re.IGNORECASE):
                cells = row.find_all(["td", "th"])
                if len(cells) >= 2:
                    for i in range(len(cells) - 1):
                        label_text = cells[i].get_text(strip=True)
                        if re.search(label_regex, label_text, re.IGNORECASE):
                            val = cells[i + 1].get_text(strip=True)
                            if val:
                                return val
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


class JaggaerScraper:
    """
    Generic scraper for the Jaggaer / SciQuest eProcurement platform's public event
    browse pages. Different state customers front their Jaggaer instance with different
    wrapper UIs (a custom agency site, a jqGrid listing, an ASP.NET search form postback,
    or the raw Jaggaer "SourcingPublicSite" module), so the state-specific behavior is
    implemented via overridable hook methods rather than hardcoded selectors.
    """

    def __init__(self, state_code: str, base_url: str, portal_name: str):
        self.state_code = state_code
        self.base_url = base_url
        self.portal_name = portal_name

    def _now(self) -> str:
        return datetime.now().isoformat()

    def _make_event_id(self, seed: str) -> str:
        return f"{self.state_code}-{hash(seed) & 0xffffffff}"

    async def scrape_all_bids(self, crawler: AsyncWebCrawler, max_pages: Optional[int] = 1) -> List[BidEvent]:
        raise NotImplementedError("Subclasses/instances must configure a list strategy")


IOWA_BID_LINKS_SCHEMA = {
    "name": "iowa_bid_links",
    "baseSelector": "a[href*='BidInfo?bidId=']",
    "fields": [{"name": "href", "type": "attribute", "attribute": "href"}],
}


class IowaJaggaerScraper(JaggaerScraper):
    """
    Iowa's bidopportunities.iowa.gov is a custom ASP.NET wrapper around the DASIowa
    Jaggaer instance. The list page renders a DataTables grid (Bid Number, Agency,
    Contact, Title, Effective Date, Expiration Date) with links to /Home/BidInfo?bidId=<guid>,
    which is itself a fully public labeled detail page.
    """

    PARSER_VERSION = "iowa-jaggaer-v1.1.0"

    def __init__(self):
        super().__init__(
            state_code="IA",
            base_url="https://bidopportunities.iowa.gov/",
            portal_name="Iowa Bid Opportunities (Jaggaer/SciQuest)",
        )

    async def scrape_all_bids(
        self, crawler: AsyncWebCrawler, max_pages: Optional[int] = 1, run_id: str = "adhoc-run"
    ) -> List[DeliveryRecord]:
        session_id = "iowa-jaggaer-session"
        results: List[DeliveryRecord] = []
        current_page = 1

        result = await fetch_page(crawler, self.base_url, schema=IOWA_BID_LINKS_SCHEMA, session_id=session_id)
        await random_delay(0.5, 1.5)

        while True:
            rows = _parse_extracted(result.extracted_content) or []
            bid_urls = []
            seen = set()
            for row in rows:
                href = row.get("href")
                if href:
                    full_url = urllib.parse.urljoin(self.base_url, href)
                    if full_url not in seen:
                        seen.add(full_url)
                        bid_urls.append(full_url)

            for url in bid_urls:
                record = await self._scrape_detail_page(crawler, url, run_id)
                if record:
                    results.append(record)

            if max_pages and current_page >= max_pages:
                break

            next_js = (
                "const btn = document.querySelector('a.paginate_button.next');"
                "if (btn && !btn.classList.contains('disabled')) { btn.click(); }"
            )
            await random_delay(0.5, 1.5)
            next_result = await fetch_page(
                crawler, self.base_url, schema=IOWA_BID_LINKS_SCHEMA, js_code=next_js,
                session_id=session_id, js_only=True,
            )
            if not next_result or next_result.extracted_content == result.extracted_content:
                break
            current_page += 1
            result = next_result

        return results

    async def _scrape_detail_page(self, crawler: AsyncWebCrawler, url: str, run_id: str = "adhoc-run") -> Optional[DeliveryRecord]:
        errors: List[str] = []
        try:
            result = await fetch_page(crawler, url)
            await random_delay(0.5, 1.0)

            parsed = urllib.parse.urlparse(url)
            bid_id = urllib.parse.parse_qs(parsed.query).get("bidId", [None])[0]
            now_str = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

            raw_html = result.html or ""
            raw_sha256 = hashlib.sha256(raw_html.encode("utf-8")).hexdigest()
            soup = _soup_from_html(raw_html)

            bid_number = get_row_label_value(soup, r'Bid\s*Number')
            title = get_row_label_value(soup, r'^Solicitation$') or bid_number or "Iowa Procurement Event"
            solicitation_type = get_row_label_value(soup, r'Solicitation\s*Type') or "Solicitation"
            agency = get_row_label_value(soup, r'^Name$') or get_row_label_value(soup, r'Agency') or "State of Iowa"

            valid_from = get_row_label_value(soup, r'^From$')
            valid_until = get_row_label_value(soup, r'^Until$')
            post_date = valid_from
            close_date = valid_until
            if not post_date:
                errors.append("published_at (From date) not found on detail page")
            if not close_date:
                errors.append("due_at (Until date) not found on detail page")

            documents: List[DeliveryDocument] = []
            try:
                seen_docs = set()
                for a_link in soup.select("a[href*='GetBidOpportunityDocument']"):
                    href = a_link.get("href")
                    text = a_link.get_text(strip=True)
                    if href and href not in seen_docs:
                        seen_docs.add(href)
                        full_download_url = urllib.parse.urljoin(url, href)
                        file_name = text or full_download_url.split("/")[-1]
                        documents.append(DeliveryDocument(
                            title=file_name,
                            url=full_download_url,
                            document_type="solicitation",
                            mime_type="application/pdf" if file_name.lower().endswith(".pdf") else None,
                            download_status="link_only",
                            storage_uri=None,
                            sha256=None,
                        ))
            except Exception:
                pass
            if documents:
                errors.append("document download/hashing not yet implemented; URLs captured as link_only")

            errors.append("raw_capture.storage_uri not set; no approved object-storage location configured")

            event_id = bid_number or bid_id or self._make_event_id(url)

            return DeliveryRecord(
                observed_at=now_str,
                source=DeliverySource(
                    name=self.portal_name,
                    base_url=self.base_url,
                    opportunity_url=url,
                    source_opportunity_id=str(event_id),
                    access_level="public",
                ),
                opportunity=DeliveryOpportunity(
                    title=title,
                    agency_name=agency,
                    solicitation_number=bid_number,
                    status="Open",
                    notice_type=solicitation_type,
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
                    parser_version=self.PARSER_VERSION,
                    record_status="partial" if errors else "ok",
                    errors=errors,
                ),
            )
        except Exception as e:
            print(f"Error scraping Iowa detail page {url}: {e}")
            return None


DELAWARE_ROW_LINKS_SCHEMA = {
    "name": "delaware_row_links",
    "baseSelector": "a[onclick*='getBidDetail']",
    "fields": [{"name": "onclick", "type": "attribute", "attribute": "onclick"}],
}


class DelawareJaggaerScraper(JaggaerScraper):
    """
    Delaware's mmp.delaware.gov/Bids/ (myMarketplace, Jaggaer-based) renders bids in a
    jqGrid (Contract Number, Contract Title, Open Date, Deadline Date, Agency Code,
    UNSPSC). Each row opens a modal via onclick=getBidDetail(<id>), but the modal footer
    exposes a "Bookmarkable Link" to /Bids/Details/<id>, a plain public detail page with
    the same labeled fields plus a Supporting Bid Documents table.
    """

    ROW_ID_RE = re.compile(r'getBidDetail\((\d+)\)')
    PARSER_VERSION = "delaware-jaggaer-v1.1.0"

    def __init__(self):
        super().__init__(
            state_code="DE",
            base_url="https://mmp.delaware.gov/Bids/",
            portal_name="Delaware myMarketplace (Jaggaer/SciQuest)",
        )

    async def scrape_all_bids(
        self, crawler: AsyncWebCrawler, max_pages: Optional[int] = 1, run_id: str = "adhoc-run"
    ) -> List[DeliveryRecord]:
        session_id = "delaware-jaggaer-session"
        results: List[DeliveryRecord] = []
        current_page = 1

        result = await fetch_page(crawler, self.base_url, schema=DELAWARE_ROW_LINKS_SCHEMA, session_id=session_id)
        await random_delay(0.5, 1.5)

        while True:
            rows = _parse_extracted(result.extracted_content) or []
            bid_ids = []
            for row in rows:
                onclick = row.get("onclick") or ""
                match = self.ROW_ID_RE.search(onclick)
                if match and match.group(1) not in bid_ids:
                    bid_ids.append(match.group(1))

            for bid_id in bid_ids:
                detail_url = urllib.parse.urljoin(self.base_url, f"Details/{bid_id}")
                record = await self._scrape_detail_page(crawler, detail_url, bid_id, run_id)
                if record:
                    results.append(record)

            if max_pages and current_page >= max_pages:
                break

            next_js = (
                "const btn = document.querySelector('#next_jqg1');"
                "if (btn && !btn.classList.contains('disabled')) { btn.click(); }"
            )
            await random_delay(0.5, 1.5)
            next_result = await fetch_page(
                crawler, self.base_url, schema=DELAWARE_ROW_LINKS_SCHEMA, js_code=next_js,
                session_id=session_id, js_only=True,
            )
            if not next_result or next_result.extracted_content == result.extracted_content:
                break
            current_page += 1
            result = next_result

        return results

    async def _scrape_detail_page(self, crawler: AsyncWebCrawler, url: str, bid_id: str, run_id: str = "adhoc-run") -> Optional[DeliveryRecord]:
        errors: List[str] = []
        try:
            result = await fetch_page(crawler, url)
            await random_delay(0.5, 1.0)

            now_str = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
            raw_html = result.html or ""
            raw_sha256 = hashlib.sha256(raw_html.encode("utf-8")).hexdigest()
            soup = _soup_from_html(raw_html)
            body_text = soup.get_text("\n", strip=True)

            title = None
            contract_number = None
            heading = soup.find(["h1", "h2", "h3"])
            if heading is not None:
                title = heading.get_text(strip=True) or None

            title_match = re.search(r'Bid Status Details for\s*\n+\s*([^\n]+)\s*\n+\s*([^\n]+)', body_text, re.IGNORECASE)
            if title_match:
                title = title_match.group(1).strip()
                contract_number = title_match.group(2).strip()
            if not title:
                title = "Delaware Procurement Event"

            ad_date_match = re.search(r'Solicitation\s*Ad\s*Date\s*\n+\s*([^\n]+)', body_text, re.IGNORECASE)
            deadline_match = re.search(r'Deadline\s*for\s*Bid\s*Responses\s*\n+\s*([^\n]+)', body_text, re.IGNORECASE)
            post_date = ad_date_match.group(1).strip() if ad_date_match else None
            close_date = deadline_match.group(1).strip() if deadline_match else None
            if not post_date:
                errors.append("published_at (Solicitation Ad Date) not found on detail page")
            if not close_date:
                errors.append("due_at (Deadline for Bid Responses) not found on detail page")

            agency_match = re.search(r'^([A-Z]{2,6})\d*[- ]', contract_number or "")
            agency = agency_match.group(1) if agency_match else "State of Delaware"

            documents: List[DeliveryDocument] = []
            try:
                for a_link in soup.select("a[href*='.pdf'], a[href*='.doc'], a[href*='.xls']"):
                    href = a_link.get("href")
                    text = a_link.get_text(strip=True)
                    if href:
                        file_name = text or href.split("/")[-1]
                        mime_type = "application/pdf" if file_name.lower().endswith(".pdf") else None
                        documents.append(DeliveryDocument(
                            title=file_name,
                            url=urllib.parse.urljoin(url, href),
                            document_type="solicitation",
                            mime_type=mime_type,
                            download_status="link_only",
                            storage_uri=None,
                            sha256=None,
                        ))
            except Exception:
                pass
            if documents:
                errors.append("document download/hashing not yet implemented; URLs captured as link_only")

            errors.append("raw_capture.storage_uri not set; no approved object-storage location configured")

            event_id = contract_number or bid_id

            return DeliveryRecord(
                observed_at=now_str,
                source=DeliverySource(
                    name=self.portal_name,
                    base_url=self.base_url,
                    opportunity_url=url,
                    source_opportunity_id=str(event_id),
                    access_level="public",
                ),
                opportunity=DeliveryOpportunity(
                    title=title,    
                    agency_name=agency,
                    solicitation_number=contract_number,
                    status="Open",
                    notice_type="Solicitation",
                    published_at=post_date,
                    due_at=close_date,
                    source_timezone="America/New_York",
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
                    parser_version=self.PARSER_VERSION,
                    record_status="partial" if errors else "ok",
                    errors=errors,
                ),
            )
        except Exception as e:
            print(f"Error scraping Delaware detail page {url}: {e}")
            return None


class PennsylvaniaJaggaerScraper(JaggaerScraper):
    """
    Pennsylvania's emarketplace.state.pa.us fronts a Jaggaer/SciQuest-based instance
    with a classic ASP.NET WebForms search page (Search.aspx). Submitting the search
    form with the Agency dropdown set to "All Using Agencies" renders a GridView of
    results (grdResults) linking to Solicitations.aspx?SID=<id>, a fully public,
    table/label-based detail page.
    """

    def __init__(self):
        super().__init__(
            state_code="PA",
            base_url="https://www.emarketplace.state.pa.us/Search.aspx",
            portal_name="Pennsylvania eMarketplace (Jaggaer/SciQuest)",
        )

    async def scrape_all_bids(self, crawler: AsyncWebCrawler, max_pages: Optional[int] = 1) -> List[BidEvent]:
        session_id = "pennsylvania-jaggaer-session"
        results: List[BidEvent] = []
        current_page = 1

        search_js = (
            "const sel = document.querySelector(\"select[id*='ddlAgency']\");"
            "if (sel) { sel.value = '46'; sel.dispatchEvent(new Event('change', { bubbles: true })); }"
            "const btn = document.querySelector(\"input[id*='btnSearch']\");"
            "if (btn) { btn.click(); }"
        )
        result = await fetch_page(crawler, self.base_url, js_code=search_js, session_id=session_id)
        await random_delay(0.5, 1.5)

        while True:
            html = result.html or ""
            soup = _soup_from_html(html)
            bid_urls = []
            seen = set()
            for link in soup.select("a[href*='Solicitations.aspx?SID=']"):
                href = link.get("href")
                if href:
                    full_url = urllib.parse.urljoin(result.url or self.base_url, href)
                    if full_url not in seen:
                        seen.add(full_url)
                        bid_urls.append(full_url)

            for url in bid_urls:
                bid = await self._scrape_detail_page(crawler, url)
                if bid:
                    results.append(bid)

            if max_pages and current_page >= max_pages:
                break

            next_js = (
                "const cands = Array.from(document.querySelectorAll('a'));"
                "const btn = cands.find(el => /next|>/i.test((el.textContent || '').trim()) && /Page\\$/.test(el.getAttribute('href') || ''));"
                "if (btn) { btn.click(); }"
            )
            await random_delay(0.5, 1.5)
            next_result = await fetch_page(
                crawler, self.base_url, js_code=next_js, session_id=session_id, js_only=True
            )
            if not next_result or not next_result.html or next_result.html == html:
                break
            current_page += 1
            result = next_result

        return results

    async def _scrape_detail_page(self, crawler: AsyncWebCrawler, url: str) -> Optional[BidEvent]:
        try:
            result = await fetch_page(crawler, url)
            await random_delay(0.5, 1.0)

            parsed = urllib.parse.urlparse(url)
            sid = urllib.parse.parse_qs(parsed.query).get("SID", [None])[0]
            now_str = self._now()
            soup = _soup_from_html(result.html)
            body_text = soup.get_text("\n", strip=True)

            title_match = re.search(r'Solicitation/Project Title:\s*([^\n\t]+)', body_text, re.IGNORECASE)
            title = title_match.group(1).strip() if title_match else "Pennsylvania Procurement Event"

            type_match = re.search(r'Types?:\s*([A-Za-z/&\s]+?)(?:\n|\t)', body_text, re.IGNORECASE)
            solicitation_type = type_match.group(1).strip() if type_match else "Solicitation"

            agency_match = re.search(r'Department/Agency:\s*([^\n\t]+)', body_text, re.IGNORECASE)
            agency = agency_match.group(1).strip() if agency_match else "Commonwealth of Pennsylvania"

            start_match = re.search(r'Solicitation Start Date:\s*([^\n\t]+)', body_text, re.IGNORECASE)
            due_match = re.search(r'Solicitation Due Date:\s*([^\n\t]+)', body_text, re.IGNORECASE)
            post_date = start_match.group(1).strip() if start_match else now_str[:10]
            close_date = due_match.group(1).strip() if due_match else now_str

            fname_match = re.search(r'First Name:\s*([^\n\t]+)', body_text, re.IGNORECASE)
            lname_match = re.search(r'Last Name:\s*([^\n\t]+)', body_text, re.IGNORECASE)
            contact_name = None
            if fname_match or lname_match:
                contact_name = " ".join(
                    part.strip() for part in [
                        fname_match.group(1) if fname_match else "",
                        lname_match.group(1) if lname_match else "",
                    ] if part.strip()
                ) or None

            contact_email = None
            email_match = EMAIL_RE.search(body_text)
            if email_match:
                contact_email = email_match.group(0)

            contact_phone = None
            phone_match = PHONE_RE.search(body_text)
            if phone_match:
                contact_phone = phone_match.group(0)

            contact = None
            if contact_name or contact_email or contact_phone:
                contact = BidContact(name=contact_name, email=contact_email, phone=contact_phone)

            attachments: List[Attachment] = []
            try:
                for a_link in soup.select("a[href*='FileDownload.aspx']"):
                    href = a_link.get("href")
                    text = a_link.get_text(strip=True)
                    if href:
                        full_download_url = urllib.parse.urljoin(url, href)
                        file_name = text or full_download_url.split("/")[-1]
                        file_type = file_name.split(".")[-1] if "." in file_name else None
                        attachments.append(Attachment(file_name=file_name, download_url=full_download_url, file_type=file_type))
            except Exception:
                pass

            event_id = sid or self._make_event_id(url)

            return BidEvent(
                event_id=str(event_id),
                state=self.state_code,
                portal_name=self.portal_name,
                title=title,
                agency=agency,
                solicitation_type=solicitation_type,
                status="Open",
                post_date=post_date,
                close_date=close_date,
                detail_url=url,
                contact=contact,
                commodity_codes=[],
                attachments=attachments,
                scraped_at=now_str,
                updated_at=now_str,
            )
        except Exception as e:
            print(f"Error scraping Pennsylvania detail page {url}: {e}")
            return None


class SourcingPublicSiteJaggaerScraper(JaggaerScraper):
    """
    Base for states whose public event browse page is the raw Jaggaer/SciQuest
    "SourcingPublicSite" module served directly from bids.sciquest.com, i.e.
    https://bids.sciquest.com/apps/Router/PublicEvent?CustomerOrg=<org>.

    Live investigation (2026-08-24) found this module renders a fully public listing
    (no login) with one <tr> per event carrying Status, Title, Description, Open/Close
    dates, Type, Number, Contact(s), and a signed "View as PDF" link -- but the
    event's own title link (id starting with SourcingPublicSite / an anchor pointing
    at app01.jaggaer.com/apps/Router/ViewSourcingEvent) redirects to a Supplier Login
    wall ("Please login to view the sourcing event"). There is no way to reach a public,
    fully-detailed HTML page per event; all extractable public data lives in the listing
    row itself plus the linked PDF. Subclasses for a customer org that turns out to be
    login-walled entirely should not be built this way -- use LoginWalledJaggaerScraper.
    """

    # IANA timezone used by the customer org, for opportunity.source_timezone.
    STATE_TIMEZONE = "America/Denver"

    def __init__(self, state_code: str, customer_org: str, portal_name: str):
        base_url = f"https://bids.sciquest.com/apps/Router/PublicEvent?CustomerOrg={customer_org}"
        super().__init__(state_code=state_code, base_url=base_url, portal_name=portal_name)
        self.customer_org = customer_org

    async def scrape_all_bids(
        self, crawler: AsyncWebCrawler, max_pages: Optional[int] = 1, run_id: str = "adhoc-run"
    ) -> List[DeliveryRecord]:
        resize_js = (
            "try { if (typeof submitSizeChange === 'function') { submitSizeChange('100','ActiveForm','','',''); } } "
            "catch (e) {}"
        )
        result = await fetch_page(
            crawler,
            self.base_url,
            js_code=resize_js,
        )

        raw_html = result.html or ""
        raw_sha256 = hashlib.sha256(raw_html.encode("utf-8")).hexdigest()

        results: List[DeliveryRecord] = []
        soup = _soup_from_html(raw_html)
        rows = soup.select("table[aria-label='Search Results'] tbody tr")

        for row_soup in rows:
            record = await self._parse_row(str(row_soup), run_id, raw_sha256)
            if record:
                results.append(record)

        return results

    async def _parse_row(self, row_html: str, run_id: str, raw_sha256: str) -> Optional[DeliveryRecord]:
        try:
            row_soup = BeautifulSoup(row_html, "lxml")
            row_text = row_soup.get_text("\n", strip=True)
            if not row_text.strip():
                return None

            now_str = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
            errors: List[str] = []

            status_match = re.search(r'^\s*(Open|Closed|Awarded|Upcoming|Cancelled)\b', row_text, re.IGNORECASE)
            status = status_match.group(1).title() if status_match else "Open"

            title = None
            title_link = row_soup.select_one("a.btn-link-header, a[href*='ViewSourcingEvent']")
            if title_link is not None:
                title = title_link.get_text(strip=True)
            if not title:
                title = "Procurement Event"

            detail_url = None
            if title_link is not None:
                detail_url = title_link.get("href")

            description_match = re.search(
                r'^\s*(?:Open|Closed|Awarded|Upcoming|Cancelled)\b[^\n]*\n' + re.escape(title) + r'\n([^\n]+)',
                row_text,
            )
            description = description_match.group(1).strip() if description_match else None

            number_match = re.search(r'Number\s*\n+\s*([^\n]+)', row_text, re.IGNORECASE)
            type_match = re.search(r'Type\s*\n+\s*([^\n]+)', row_text, re.IGNORECASE)
            # The "Open" date label is preceded by the row's own status word (also "Open"
            # when the opportunity is open), so anchor on a date-shaped value rather than
            # taking the first "Open"-adjacent line, which previously grabbed the title.
            open_match = re.search(r'Open\s*\n\s*\t?\s*\n\s*(\d{1,2}/\d{1,2}/\d{4}[^\n]*)', row_text, re.IGNORECASE)
            close_match = re.search(r'Close\s*\n\s*\t?\s*\n\s*(\d{1,2}/\d{1,2}/\d{4}[^\n]*)', row_text, re.IGNORECASE)
            contact_match = re.search(r'Contact\s*\n+\s*([^\n]+)', row_text, re.IGNORECASE)

            bid_number = number_match.group(1).strip() if number_match else None
            solicitation_type = type_match.group(1).strip() if type_match else "Solicitation"
            post_date = open_match.group(1).strip() if open_match else None
            close_date = close_match.group(1).strip() if close_match else None
            if not post_date:
                errors.append("published_at (Open date) not found in row text")
            if not close_date:
                errors.append("due_at (Close date) not found in row text")

            contact_name = None
            contact_email = None
            if contact_match:
                contact_line = contact_match.group(1).strip()
                email_match = EMAIL_RE.search(contact_line)
                if email_match:
                    contact_email = email_match.group(0)
                    contact_name = contact_line.replace(contact_email, "").strip() or None
                else:
                    contact_name = contact_line or None

            documents: List[DeliveryDocument] = []
            pdf_link = row_soup.select_one("a[id*='BUTTON_PDF_VIEW']")
            if pdf_link is not None:
                pdf_href = pdf_link.get("href")
                if pdf_href:
                    documents.append(DeliveryDocument(
                        title=f"{title} (View as PDF)",
                        url=pdf_href,
                        document_type="solicitation",
                        mime_type="application/pdf",
                        download_status="link_only",
                        storage_uri=None,
                        sha256=None,
                    ))
            if documents:
                errors.append("document download/hashing not yet implemented; URL captured as link_only")

            event_id = bid_number or self._make_event_id(detail_url or title)
            opportunity_url = detail_url or self.base_url

            errors.append(
                f"{self.portal_name} detail page requires a Jaggaer supplier login; "
                "description/full detail beyond the listing row is unavailable"
            )
            errors.append("raw_capture.storage_uri not set; no approved object-storage location configured")

            return DeliveryRecord(
                observed_at=now_str,
                source=DeliverySource(
                    name=self.portal_name,
                    base_url=self.base_url,
                    opportunity_url=opportunity_url,
                    source_opportunity_id=str(event_id),
                    access_level="registration_required" if detail_url else "public",
                ),
                opportunity=DeliveryOpportunity(
                    title=title,
                    agency_name=self.portal_name,
                    solicitation_number=bid_number,
                    status=status,
                    notice_type=solicitation_type,
                    published_at=post_date,
                    due_at=close_date,
                    source_timezone=self.STATE_TIMEZONE,
                    description=description,
                    categories=[],
                    submission_url=opportunity_url,
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
                    parser_version=f"{self.state_code.lower()}-jaggaer-sourcingpublicsite-v1.1.0",
                    record_status="partial",
                    errors=errors,
                ),
            )
        except Exception as e:
            print(f"Error parsing {self.state_code} SourcingPublicSite row: {e}")
            return None


class LoginWalledJaggaerScraper(JaggaerScraper):
    """
    Used for a Jaggaer/SciQuest customer org whose public event router redirects
    straight to a Supplier Login wall with no accessible listing at all, rather than
    only gating the per-event detail page. scrape_all_bids raises immediately instead
    of fabricating a successful (empty) scrape.
    """

    def __init__(self, state_code: str, base_url: str, portal_name: str, reason: str):
        super().__init__(state_code=state_code, base_url=base_url, portal_name=portal_name)
        self.reason = reason

    async def scrape_all_bids(self, crawler: AsyncWebCrawler, max_pages: Optional[int] = 1) -> List[BidEvent]:
        raise JaggaerLoginRequiredError(
            f"{self.portal_name} ({self.state_code}) requires a Jaggaer supplier login to "
            f"view its public event list at {self.base_url}: {self.reason}"
        )


class UtahJaggaerScraper(SourcingPublicSiteJaggaerScraper):
    """
    Live investigation (2026-08-24) confirmed https://bids.sciquest.com/apps/Router/
    PublicEvent?CustomerOrg=StateOfUtah renders a fully public "Business Opportunities"
    listing (Open/Upcoming/Closed/Awarded/All) with no login required. Each row's
    title link ("ViewSourcingEvent?AuthToken=...") redirects to a Jaggaer supplier
    login wall, so only listing-level fields plus the public "View as PDF" link are
    extractable without an account.
    """

    def __init__(self):
        super().__init__(
            state_code="UT",
            customer_org="StateOfUtah",
            portal_name="Utah Public Procurement Place (Jaggaer/SciQuest)",
        )


class MontanaJaggaerScraper(SourcingPublicSiteJaggaerScraper):
    """
    Live investigation (2026-08-24) confirmed https://bids.sciquest.com/apps/Router/
    PublicEvent?CustomerOrg=StateOfMontana (linked from spb.mt.gov/eMACS-Resources as
    "eMACS Access") renders the same public Jaggaer SourcingPublicSite listing as Utah,
    with no login required to browse it. As with Utah, the per-row title link redirects
    to a Jaggaer supplier login wall for the full detail view.
    """

    def __init__(self):
        super().__init__(
            state_code="MT",
            customer_org="StateOfMontana",
            portal_name="Montana eMACS (Jaggaer/SciQuest)",
        )
