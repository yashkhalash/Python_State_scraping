import re
import urllib.parse
from datetime import datetime
from typing import List, Optional
from playwright.async_api import BrowserContext
from src.storage import BidEvent, BidContact, CommodityCode, Attachment
from src.stealth import random_delay

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

    async def scrape_all_bids(self, context: BrowserContext, max_pages: Optional[int] = 1) -> List[BidEvent]:
        page = await context.new_page()
        try:
            await page.goto(self.BROWSE_URL, wait_until="domcontentloaded", timeout=30000)
            await page.wait_for_timeout(3000)
            await random_delay(0.5, 1.5)

            results: List[BidEvent] = []
            current_page = 1

            while True:
                rows = await page.locator("table[id*='grid'] tbody tr, table.iv-grid-view tbody tr").all()
                row_data = []
                for row in rows:
                    cells = await row.locator("td").all()
                    cell_texts = [(await c.inner_text()).strip() for c in cells]
                    link = row.locator("a[href]").first
                    href = None
                    if await link.count() > 0:
                        href = await link.get_attribute("href")
                    if href:
                        full_url = urllib.parse.urljoin(self.base_url + "/", href)
                        row_data.append((full_url, cell_texts))

                if not row_data:
                    break

                for url, cell_texts in row_data:
                    bid = await self._scrape_detail_page(context, url, cell_texts)
                    if bid:
                        results.append(bid)

                if max_pages and current_page >= max_pages:
                    break

                next_btn = page.locator(
                    "[aria-label*='Next page'], a:has-text('Next'), button:has-text('Next')"
                ).first
                if await next_btn.count() > 0 and await next_btn.is_visible() and await next_btn.is_enabled():
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

    async def _scrape_detail_page(self, context: BrowserContext, url: str, cell_texts: Optional[List[str]] = None) -> Optional[BidEvent]:
        cell_texts = cell_texts or []
        grid_fields = self._parse_grid_row(cell_texts)
        detail_page = await context.new_page()
        try:
            await detail_page.goto(url, wait_until="domcontentloaded", timeout=30000)
            await detail_page.wait_for_timeout(1500)
            await random_delay(0.5, 1.0)

            event_id = self._extract_event_id(url)
            now_str = datetime.now().isoformat()

            if "/bas/browser_check" in detail_page.url or "captcha" in (await detail_page.content()).lower():
                return self._build_bid_from_grid(url, event_id, grid_fields, now_str)

            async def get_label_value(label_regex: str) -> Optional[str]:
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

            body_text = await detail_page.locator("body").inner_text()

            title = await get_label_value(r'Title|Label|Object|Description') or grid_fields.get("title")
            if not title:
                heading = detail_page.locator("h1, h2, h3").first
                if await heading.count() > 0:
                    txt = (await heading.inner_text()).strip()
                    if txt:
                        title = txt
            if not title:
                title = f"{self.portal_name} Solicitation"

            agency = (
                await get_label_value(r'Agency|Buyer|Department|Issuing\s*Entity|Organization')
                or grid_fields.get("agency")
                or f"State of {self.state_code}"
            )
            solicitation_type = await get_label_value(r'Type|Process\s*Type|Rfx\s*Type') or "RFP"
            status = await get_label_value(r'Status') or grid_fields.get("status") or "Open"
            post_date = (
                await get_label_value(r'Publication\s*(?:begin\s*)?[Dd]ate|Post\s*Date|Begin\s*Date|Start\s*Date')
                or grid_fields.get("post_date")
                or now_str[:10]
            )
            close_date = (
                await get_label_value(r'Publication\s*end\s*[Dd]ate|Close\s*Date|End\s*Date|Due\s*Date|Submission\s*[Dd]ate')
                or grid_fields.get("close_date")
                or now_str
            )

            contact_name = await get_label_value(r'Contact\s*Name|Buyer\s*Name|Contact')
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

            commodity_codes: List[CommodityCode] = []
            try:
                comm_rows = await detail_page.locator("table tr").all()
                for row in comm_rows:
                    cells = await row.locator("td").all()
                    if len(cells) >= 2:
                        c_code = (await cells[0].inner_text()).strip()
                        c_desc = (await cells[1].inner_text()).strip()
                        if re.match(r'^\d{3,8}$', c_code) and c_desc:
                            commodity_codes.append(CommodityCode(code=c_code, description=c_desc))
            except Exception:
                pass

            attachments: List[Attachment] = []
            try:
                att_links = await detail_page.locator(
                    "a[href*='download'], a[href*='file'], a[href*='.pdf'], a[href*='.doc'], a[href*='attachment']"
                ).all()
                for a_link in att_links:
                    href = await a_link.get_attribute("href")
                    link_text = (await a_link.inner_text()).strip()
                    if not href:
                        continue
                    file_name = link_text or href.split("/")[-1] or "attachment"
                    full_download_url = urllib.parse.urljoin(self.base_url + "/", href)
                    file_type = file_name.split(".")[-1] if "." in file_name else None
                    attachments.append(
                        Attachment(file_name=file_name, download_url=full_download_url, file_type=file_type)
                    )
            except Exception:
                pass

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
        finally:
            await detail_page.close()

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
