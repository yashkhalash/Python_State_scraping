import hashlib
import re
import urllib.parse
from datetime import datetime, timezone
from typing import List, Optional
from playwright.async_api import BrowserContext, Page
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
from src.stealth import random_delay

EMAIL_RE = re.compile(r'[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}')
PHONE_RE = re.compile(r'\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}')


class JaggaerLoginRequiredError(Exception):
    """Raised when a Jaggaer/SciQuest portal's public event router redirects to a supplier login wall."""
    pass


async def get_row_label_value(page: Page, label_regex: str) -> Optional[str]:
    """
    Extracts a field value from Bootstrap-style label/value rows, i.e.
    <div class="row"><div class="col-*"><label>Label</label></div>
    <div class="col-*">Value</div></div>, as used by Iowa's bidopportunities.iowa.gov
    detail page.
    """
    try:
        rows = await page.locator("div.row").all()
        for row in rows:
            label_el = row.locator("label").first
            if await label_el.count() == 0:
                continue
            label_text = (await label_el.inner_text()).strip()
            if re.search(label_regex, label_text, re.IGNORECASE):
                value_el = row.locator("div[class*='col-']").last
                if await value_el.count() > 0:
                    val = (await value_el.inner_text()).strip()
                    if val:
                        return val
    except Exception:
        pass
    return None


async def get_label_value(page: Page, label_regex: str) -> Optional[str]:
    try:
        rows = await page.locator("tr").all()
        for row in rows:
            row_text = await row.inner_text()
            if re.search(label_regex, row_text, re.IGNORECASE):
                cells = await row.locator("td, th").all()
                if len(cells) >= 2:
                    for i in range(len(cells) - 1):
                        label_text = (await cells[i].inner_text()).strip()
                        if re.search(label_regex, label_text, re.IGNORECASE):
                            val = (await cells[i + 1].inner_text()).strip()
                            if val:
                                return val
                    val = (await cells[1].inner_text()).strip()
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

    async def scrape_all_bids(self, context: BrowserContext, max_pages: Optional[int] = 1) -> List[BidEvent]:
        raise NotImplementedError("Subclasses/instances must configure a list strategy")


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

    async def scrape_all_bids(self, context: BrowserContext, max_pages: Optional[int] = 1, run_id: str = "adhoc-run") -> List[DeliveryRecord]:
        page = await context.new_page()
        try:
            await page.goto(self.base_url, wait_until="domcontentloaded", timeout=30000)
            await page.wait_for_timeout(3000)
            await random_delay(0.5, 1.5)

            results: List[DeliveryRecord] = []
            current_page = 1

            while True:
                links = await page.locator("a[href*='BidInfo?bidId=']").all()
                bid_urls = []
                seen = set()
                for link in links:
                    href = await link.get_attribute("href")
                    if href:
                        full_url = urllib.parse.urljoin(self.base_url, href)
                        if full_url not in seen:
                            seen.add(full_url)
                            bid_urls.append(full_url)

                for url in bid_urls:
                    record = await self._scrape_detail_page(context, url, run_id)
                    if record:
                        results.append(record)

                if max_pages and current_page >= max_pages:
                    break

                next_btn = page.locator("a.paginate_button.next").first
                if await next_btn.count() > 0:
                    classes = await next_btn.get_attribute("class") or ""
                    if "disabled" in classes:
                        break
                    current_page += 1
                    await next_btn.click()
                    await page.wait_for_timeout(1500)
                    await random_delay(0.5, 1.5)
                else:
                    break

            return results
        finally:
            await page.close()

    async def _scrape_detail_page(self, context: BrowserContext, url: str, run_id: str = "adhoc-run") -> Optional[DeliveryRecord]:
        detail_page = await context.new_page()
        errors: List[str] = []
        try:
            await detail_page.goto(url, wait_until="domcontentloaded", timeout=30000)
            await detail_page.wait_for_timeout(1000)
            await random_delay(0.5, 1.0)

            parsed = urllib.parse.urlparse(url)
            bid_id = urllib.parse.parse_qs(parsed.query).get("bidId", [None])[0]
            now_str = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

            raw_html = await detail_page.content()
            raw_sha256 = hashlib.sha256(raw_html.encode("utf-8")).hexdigest()

            bid_number = await get_row_label_value(detail_page, r'Bid\s*Number')
            title = await get_row_label_value(detail_page, r'^Solicitation$') or bid_number or "Iowa Procurement Event"
            solicitation_type = await get_row_label_value(detail_page, r'Solicitation\s*Type') or "Solicitation"
            agency = await get_row_label_value(detail_page, r'^Name$') or await get_row_label_value(detail_page, r'Agency') or "State of Iowa"

            valid_from = await get_row_label_value(detail_page, r'^From$')
            valid_until = await get_row_label_value(detail_page, r'^Until$')
            post_date = valid_from
            close_date = valid_until
            if not post_date:
                errors.append("published_at (From date) not found on detail page")
            if not close_date:
                errors.append("due_at (Until date) not found on detail page")

            documents: List[DeliveryDocument] = []
            try:
                seen_docs = set()
                doc_links = await detail_page.locator("a[href*='GetBidOpportunityDocument']").all()
                for a_link in doc_links:
                    href = await a_link.get_attribute("href")
                    text = (await a_link.inner_text()).strip()
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
        finally:
            await detail_page.close()


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

    async def scrape_all_bids(self, context: BrowserContext, max_pages: Optional[int] = 1, run_id: str = "adhoc-run") -> List[DeliveryRecord]:
        page = await context.new_page()
        try:
            await page.goto(self.base_url, wait_until="domcontentloaded", timeout=30000)
            await page.wait_for_timeout(3000)
            await random_delay(0.5, 1.5)

            results: List[DeliveryRecord] = []
            current_page = 1

            while True:
                row_links = await page.locator("a[onclick*='getBidDetail']").all()
                bid_ids = []
                for link in row_links:
                    onclick = await link.get_attribute("onclick") or ""
                    match = self.ROW_ID_RE.search(onclick)
                    if match and match.group(1) not in bid_ids:
                        bid_ids.append(match.group(1))

                for bid_id in bid_ids:
                    detail_url = urllib.parse.urljoin(self.base_url, f"Details/{bid_id}")
                    record = await self._scrape_detail_page(context, detail_url, bid_id, run_id)
                    if record:
                        results.append(record)

                if max_pages and current_page >= max_pages:
                    break

                next_btn = page.locator("#next_jqg1").first
                if await next_btn.count() > 0:
                    classes = await next_btn.get_attribute("class") or ""
                    if "disabled" in classes:
                        break
                    current_page += 1
                    await next_btn.click()
                    await page.wait_for_timeout(1500)
                    await random_delay(0.5, 1.5)
                else:
                    break

            return results
        finally:
            await page.close()

    async def _scrape_detail_page(self, context: BrowserContext, url: str, bid_id: str, run_id: str = "adhoc-run") -> Optional[DeliveryRecord]:
        detail_page = await context.new_page()
        errors: List[str] = []
        try:
            await detail_page.goto(url, wait_until="domcontentloaded", timeout=30000)
            await detail_page.wait_for_timeout(1000)
            await random_delay(0.5, 1.0)

            now_str = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
            body_text = await detail_page.locator("body").inner_text()

            raw_html = await detail_page.content()
            raw_sha256 = hashlib.sha256(raw_html.encode("utf-8")).hexdigest()

            title = None
            contract_number = None
            heading = detail_page.locator("h1, h2, h3").first
            if await heading.count() > 0:
                title = (await heading.inner_text()).strip() or None

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
                doc_links = await detail_page.locator("a[href*='.pdf'], a[href*='.doc'], a[href*='.xls']").all()
                for a_link in doc_links:
                    href = await a_link.get_attribute("href")
                    text = (await a_link.inner_text()).strip()
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
        finally:
            await detail_page.close()


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

    async def scrape_all_bids(self, context: BrowserContext, max_pages: Optional[int] = 1) -> List[BidEvent]:
        page = await context.new_page()
        try:
            await page.goto(self.base_url, wait_until="domcontentloaded", timeout=30000)
            await page.wait_for_timeout(2000)
            await random_delay(0.5, 1.5)

            agency_dropdown = page.locator("select[id*='ddlAgency']").first
            if await agency_dropdown.count() > 0:
                try:
                    await agency_dropdown.select_option("46")
                except Exception:
                    pass

            search_btn = page.locator("input[id*='btnSearch']").first
            if await search_btn.count() > 0:
                await search_btn.click()
                await page.wait_for_load_state("domcontentloaded")
                await page.wait_for_timeout(3000)
                await random_delay(0.5, 1.5)

            results: List[BidEvent] = []
            current_page = 1

            while True:
                links = await page.locator("a[href*='Solicitations.aspx?SID=']").all()
                bid_urls = []
                seen = set()
                for link in links:
                    href = await link.get_attribute("href")
                    if href:
                        full_url = urllib.parse.urljoin(page.url, href)
                        if full_url not in seen:
                            seen.add(full_url)
                            bid_urls.append(full_url)

                for url in bid_urls:
                    bid = await self._scrape_detail_page(context, url)
                    if bid:
                        results.append(bid)

                if max_pages and current_page >= max_pages:
                    break

                next_btn = page.locator("a:has-text('Next'), a[href*=\"Page$\"]:has-text('>')").first
                if await next_btn.count() > 0 and await next_btn.is_visible():
                    current_page += 1
                    await next_btn.click()
                    await page.wait_for_load_state("domcontentloaded")
                    await page.wait_for_timeout(2000)
                    await random_delay(0.5, 1.5)
                else:
                    break

            return results
        finally:
            await page.close()

    async def _scrape_detail_page(self, context: BrowserContext, url: str) -> Optional[BidEvent]:
        detail_page = await context.new_page()
        try:
            await detail_page.goto(url, wait_until="domcontentloaded", timeout=30000)
            await detail_page.wait_for_timeout(1000)
            await random_delay(0.5, 1.0)

            parsed = urllib.parse.urlparse(url)
            sid = urllib.parse.parse_qs(parsed.query).get("SID", [None])[0]
            now_str = self._now()
            body_text = await detail_page.locator("body").inner_text()

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
                att_links = await detail_page.locator("a[href*='FileDownload.aspx']").all()
                for a_link in att_links:
                    href = await a_link.get_attribute("href")
                    text = (await a_link.inner_text()).strip()
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
        finally:
            await detail_page.close()


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

    async def scrape_all_bids(self, context: BrowserContext, max_pages: Optional[int] = 1, run_id: str = "adhoc-run") -> List[DeliveryRecord]:
        page = await context.new_page()
        try:
            await page.goto(self.base_url, wait_until="domcontentloaded", timeout=30000)
            await page.wait_for_timeout(4000)
            await random_delay(0.5, 1.5)

            try:
                await page.evaluate("submitSizeChange('100','ActiveForm','','','');")
                await page.wait_for_timeout(3000)
                await random_delay(0.5, 1.0)
            except Exception:
                pass

            raw_html = await page.content()
            raw_sha256 = hashlib.sha256(raw_html.encode("utf-8")).hexdigest()

            results: List[DeliveryRecord] = []
            rows = await page.locator("table[aria-label='Search Results'] tbody tr").all()

            for row in rows:
                record = await self._parse_row(row, run_id, raw_sha256)
                if record:
                    results.append(record)

            return results
        finally:
            await page.close()

    async def _parse_row(self, row, run_id: str, raw_sha256: str) -> Optional[DeliveryRecord]:
        try:
            row_text = await row.inner_text()
            if not row_text.strip():
                return None

            now_str = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
            errors: List[str] = []

            status_match = re.search(r'^\s*(Open|Closed|Awarded|Upcoming|Cancelled)\b', row_text, re.IGNORECASE)
            status = status_match.group(1).title() if status_match else "Open"

            title = None
            title_link = row.locator("a.btn-link-header, a[href*='ViewSourcingEvent']").first
            if await title_link.count() > 0:
                title = (await title_link.inner_text()).strip()
            if not title:
                title = "Procurement Event"

            detail_url = None
            if await title_link.count() > 0:
                detail_url = await title_link.get_attribute("href")

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
            pdf_link = row.locator("a[id*='BUTTON_PDF_VIEW']").first
            if await pdf_link.count() > 0:
                pdf_href = await pdf_link.get_attribute("href")
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

    async def scrape_all_bids(self, context: BrowserContext, max_pages: Optional[int] = 1) -> List[BidEvent]:
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
