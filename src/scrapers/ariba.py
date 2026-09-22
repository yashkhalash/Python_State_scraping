import re
import urllib.parse
from datetime import datetime
from typing import List, Optional
from playwright.async_api import BrowserContext, Page
from src.storage import BidEvent, BidContact, CommodityCode, Attachment
from src.stealth import random_delay

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

    async def _prepare_search(self, page: Page) -> None:
        pass

    async def _collect_result_links(self, page: Page) -> List[str]:
        links = await page.locator("a[href]").all()
        urls = []
        seen = set()
        for link in links:
            href = await link.get_attribute("href")
            if not href:
                continue
            full_url = urllib.parse.urljoin(self.base_url + "/", href)
            if full_url not in seen:
                seen.add(full_url)
                urls.append(full_url)
        return urls

    async def _go_to_next_page(self, page: Page) -> bool:
        next_btn = page.locator("a:has-text('Next'), button:has-text('Next')").first
        if await next_btn.count() > 0 and await next_btn.is_visible() and await next_btn.is_enabled():
            await next_btn.click()
            await page.wait_for_load_state("domcontentloaded")
            await page.wait_for_timeout(2000)
            await random_delay(0.5, 1.5)
            return True
        return False

    async def scrape_all_bids(self, context: BrowserContext, max_pages: Optional[int] = 1) -> List[BidEvent]:
        page = await context.new_page()
        try:
            await page.goto(self.LIST_URL, wait_until="domcontentloaded", timeout=30000)
            await page.wait_for_timeout(2000)
            await random_delay(1.0, 2.0)

            await self._prepare_search(page)

            results: List[BidEvent] = []
            current_page = 1

            while True:
                event_urls = await self._collect_result_links(page)
                if not event_urls:
                    break

                for url in event_urls:
                    detail_bid = await self._scrape_detail_page(context, url)
                    if detail_bid:
                        results.append(detail_bid)

                if max_pages and current_page >= max_pages:
                    break

                current_page += 1
                if not await self._go_to_next_page(page):
                    break

            return results
        finally:
            await page.close()

    async def _get_label_value(self, detail_page: Page, label_regex: str) -> Optional[str]:
        try:
            rows = await detail_page.locator("tr").all()
            for row in rows:
                row_text = await row.inner_text()
                if re.search(label_regex, row_text, re.IGNORECASE):
                    cells = await row.locator("td, th").all()
                    if len(cells) >= 2:
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

    async def _extract_detail_fields(self, detail_page: Page, url: str) -> dict:
        raise NotImplementedError

    async def _scrape_detail_page(self, context: BrowserContext, url: str) -> Optional[BidEvent]:
        detail_page = await context.new_page()
        try:
            await detail_page.goto(url, wait_until="domcontentloaded", timeout=30000)
            await detail_page.wait_for_timeout(1500)
            await random_delay(0.5, 1.5)

            now_str = datetime.now().isoformat()
            fields = await self._extract_detail_fields(detail_page, url)

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
        finally:
            await detail_page.close()


class FloridaAribaScraper(AribaScraper):
    def __init__(self):
        super().__init__(
            state_code="FL",
            base_url="https://vendor.myfloridamarketplace.com",
            portal_name="MyFloridaMarketPlace Vendor Bid System (VBS)",
            search_entry_path="/search/bids",
            agency_fallback="State of Florida",
        )

    async def _prepare_search(self, page: Page) -> None:
        status_header = page.locator("text=Ad Status").first
        if await status_header.count() > 0:
            await status_header.click()
            await page.wait_for_timeout(500)
        open_checkbox = page.locator("text=OPEN").first
        if await open_checkbox.count() > 0:
            await open_checkbox.click()
            await page.wait_for_timeout(500)
        search_btn = page.locator("button:has-text('Search')").first
        if await search_btn.count() > 0:
            await search_btn.click()
            await page.wait_for_timeout(3000)
            await random_delay(1.0, 2.0)

    async def _collect_result_links(self, page: Page) -> List[str]:
        links = await page.locator("table a[href], table button, table a").all()
        urls = []
        seen = set()
        rows = await page.locator("table tr").all()
        for row in rows:
            link = row.locator("a").first
            if await link.count() == 0:
                continue
            href = await link.get_attribute("href")
            link_text = (await link.inner_text()).strip()
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

    async def _scrape_detail_page(self, context: BrowserContext, url: str) -> Optional[BidEvent]:
        detail_page = await context.new_page()
        try:
            await detail_page.goto(url, wait_until="domcontentloaded", timeout=30000)
            await detail_page.wait_for_timeout(2500)
            await random_delay(0.5, 1.5)

            now_str = datetime.now().isoformat()
            fields = await self._extract_detail_fields(detail_page, url)

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
        finally:
            await detail_page.close()

    async def _go_to_next_page(self, page: Page) -> bool:
        next_btn = page.locator("button[aria-label='Next page']").first
        if await next_btn.count() > 0 and await next_btn.is_enabled():
            await next_btn.click()
            await page.wait_for_timeout(2500)
            await random_delay(0.5, 1.5)
            return True
        return False

    async def _extract_detail_fields(self, detail_page: Page, url: str) -> dict:
        body_text = await detail_page.locator("body").inner_text()

        event_id = None
        ad_number_match = re.search(r'Advertisement\s*Number:\s*([A-Za-z0-9-]+)', body_text, re.IGNORECASE)
        if ad_number_match:
            event_id = ad_number_match.group(1).strip()

        title = None
        h1 = detail_page.locator("h1").first
        if await h1.count() > 0:
            txt = (await h1.inner_text()).strip()
            if txt:
                title = txt

        agency = None
        agency_span = detail_page.locator("h1.mat-headline + span").first
        if await agency_span.count() > 0:
            txt = (await agency_span.inner_text()).strip()
            if txt and "Last Edit" not in txt:
                agency = txt

        solicitation_type = None
        type_span = detail_page.locator("span.mat-headline.f-sm").first
        if await type_span.count() > 0:
            txt = (await type_span.inner_text()).strip()
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
            comm_rows = await detail_page.locator("mfmp-commodity-codes-list tr, table tr").all()
            for row in comm_rows:
                cells = await row.locator("td").all()
                if len(cells) >= 2:
                    c_code = (await cells[0].inner_text()).strip()
                    c_desc = (await cells[1].inner_text()).strip()
                    if re.match(r'^\d{4,10}$', c_code) and c_desc:
                        commodity_codes.append(CommodityCode(code=c_code, description=c_desc))
        except Exception:
            pass

        attachments: List[Attachment] = []
        try:
            att_rows = await detail_page.locator("table tr").all()
            for row in att_rows:
                link = row.locator("a.document-link, a[href*='download'], a[href*='.pdf'], a[href*='.doc']").first
                if await link.count() == 0:
                    continue
                file_name = (await link.inner_text()).strip()
                href = await link.get_attribute("href")
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


class NorthCarolinaAribaScraper(AribaScraper):
    def __init__(self):
        super().__init__(
            state_code="NC",
            base_url="https://evp.nc.gov",
            portal_name="NC eProcurement Electronic Vendor Portal (eVP)",
            search_entry_path="/solicitations/",
            agency_fallback="State of North Carolina",
        )

    async def _collect_result_links(self, page: Page) -> List[str]:
        links = await page.locator("a.details-link, a[href*='/solicitations/details']").all()
        urls = []
        seen = set()
        for link in links:
            href = await link.get_attribute("href")
            if not href:
                continue
            full_url = urllib.parse.urljoin(self.base_url + "/", href)
            if full_url not in seen:
                seen.add(full_url)
                urls.append(full_url)
        return urls

    async def _go_to_next_page(self, page: Page) -> bool:
        next_btn = page.locator("a.entity-pager-next-link").first
        if await next_btn.count() > 0 and await next_btn.is_visible():
            await next_btn.click()
            await page.wait_for_timeout(2500)
            await random_delay(0.5, 1.5)
            return True
        return False

    async def _field_input_value(self, detail_page: Page, field_id: str) -> Optional[str]:
        try:
            field = detail_page.locator(f"#{field_id}")
            if await field.count() > 0:
                val = await field.input_value()
                if val and val.strip():
                    return val.strip()
        except Exception:
            pass
        return None

    async def _extract_detail_fields(self, detail_page: Page, url: str) -> dict:
        event_id = await self._field_input_value(detail_page, "evp_solicitationnbr")

        title = None
        h1 = detail_page.locator("h1").first
        if await h1.count() > 0:
            txt = (await h1.inner_text()).strip()
            if txt:
                title = txt

        agency = await self._field_input_value(detail_page, "evp_department_name") \
            or await self._get_label_value(detail_page, r'Department')

        solicitation_type = await self._get_label_value(detail_page, r'Solicitation\s*Type')
        status = await self._get_label_value(detail_page, r'Status\s*Reason|Status')
        post_date = await self._get_label_value(detail_page, r'Opening\s*Date')
        close_date = await self._get_label_value(detail_page, r'Posted\s*Date')

        description = await self._get_label_value(detail_page, r'Description')

        commodity_codes: List[CommodityCode] = []
        primary_commodity = await self._get_label_value(detail_page, r'Primary\s*Commodity\s*Code')
        if primary_commodity:
            code_match = re.search(r'(\d{4,10})\s*$', primary_commodity)
            if code_match:
                desc = primary_commodity[: code_match.start()].strip(" -")
                commodity_codes.append(CommodityCode(code=code_match.group(1), description=desc or primary_commodity))

        try:
            comm_rows = await detail_page.locator("#Subgrid_new_1 table tr, .subgrid table tr").all()
            for row in comm_rows:
                cells = await row.locator("td").all()
                if len(cells) >= 2:
                    c_code = (await cells[0].inner_text()).strip()
                    c_desc = (await cells[1].inner_text()).strip()
                    if re.match(r'^\d{4,10}$', c_code) and c_desc:
                        commodity_codes.append(CommodityCode(code=c_code, description=c_desc))
        except Exception:
            pass

        contact_name = None
        contact_email = None
        contact_phone = None
        special_instructions = await self._get_label_value(detail_page, r'Special\s*Instructions')
        owner = await self._field_input_value(detail_page, "evp_owneridname") \
            or await self._get_label_value(detail_page, r'Owner')
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
            att_links = await detail_page.locator(".entity-notes a[href*='/_entity/annotation']").all()
            for a_link in att_links:
                href = await a_link.get_attribute("href")
                link_text = (await a_link.inner_text()).strip()
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
