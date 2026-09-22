import re
import urllib.parse
from datetime import datetime
from typing import List, Optional
from playwright.async_api import BrowserContext, Page
from src.storage import BidEvent, BidContact, CommodityCode, Attachment
from src.stealth import random_delay

class GeorgiaScraper:
    BASE_URL = "https://ssl.doas.state.ga.us/gpr/"

    async def scrape_all_bids(self, context: BrowserContext, max_pages: Optional[int] = 1) -> List[BidEvent]:
        page = await context.new_page()
        try:
            await page.goto(self.BASE_URL, wait_until="domcontentloaded", timeout=30000)
            await random_delay(1.0, 2.0)

            # Click Search button if present on GPR homepage to load initial results list
            search_btn = page.locator("input[type='submit'][value*='Search'], button:has-text('Search')").first
            if await search_btn.is_visible():
                await search_btn.click()
                await page.wait_for_load_state("domcontentloaded")
                await random_delay(1.0, 2.0)

            results: List[BidEvent] = []
            current_page = 1

            while True:
                # Find detail links on current page
                links = await page.locator("a[href*='eventDetails']").all()
                event_urls = []
                for link in links:
                    href = await link.get_attribute("href")
                    if href:
                        full_url = urllib.parse.urljoin(self.BASE_URL, href)
                        if full_url not in event_urls:
                            event_urls.append(full_url)

                if not event_urls:
                    break

                for url in event_urls:
                    detail_bid = await self._scrape_detail_page(context, url)
                    if detail_bid:
                        results.append(detail_bid)

                if max_pages and current_page >= max_pages:
                    break

                # Pagination check
                next_btn = page.locator("a:has-text('Next'), button:has-text('Next'), a:has-text('>')").first
                if await next_btn.is_visible() and await next_btn.is_enabled():
                    current_page += 1
                    await next_btn.click()
                    await page.wait_for_load_state("domcontentloaded")
                    await random_delay(1.0, 2.0)
                else:
                    break

            return results
        finally:
            await page.close()

    async def _scrape_detail_page(self, context: BrowserContext, url: str) -> Optional[BidEvent]:
        detail_page = await context.new_page()
        try:
            await detail_page.goto(url, wait_until="domcontentloaded", timeout=30000)
            await random_delay(0.5, 1.5)

            # Parse event ID from URL or page text
            parsed_url = urllib.parse.urlparse(url)
            query_params = urllib.parse.parse_qs(parsed_url.query)
            event_id = query_params.get("eventId", [None])[0]
            
            content_text = await detail_page.content()

            if not event_id:
                eid_match = re.search(r'Event\s*ID[:\s]*([A-Za-z0-9_-]+)', content_text, re.IGNORECASE)
                event_id = eid_match.group(1) if eid_match else f"GA-{hash(url) & 0xffffffff}"

            now_str = datetime.now().isoformat()

            # Helper for label extraction
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

            title = await get_label_value(r'Title|Event\s*Name')
            if not title:
                h1_elements = await detail_page.locator("h1, h2, h3").all()
                for h in h1_elements:
                    txt = (await h.inner_text()).strip()
                    if txt and "Event Details" not in txt:
                        title = txt
                        break
            if not title:
                title = "Georgia Procurement Event"

            agency = await get_label_value(r'Agency|Department|Issuing\s*Entity') or "State of Georgia"
            solicitation_type = await get_label_value(r'Solicitation\s*Type|Event\s*Type|Type') or "Solicitation"
            status = await get_label_value(r'Status') or "Open"
            post_date = await get_label_value(r'Post\s*Date|Issue\s*Date|Start\s*Date') or now_str[:10]
            close_date = await get_label_value(r'Close\s*Date|End\s*Date|Due\s*Date') or now_str

            # Extract Contact details
            contact_name = await get_label_value(r'Contact\s*Name|Contact')
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
                contact = BidContact(
                    name=contact_name,
                    email=contact_email,
                    phone=contact_phone
                )

            # Extract Commodity codes
            commodity_codes = []
            try:
                comm_rows = await detail_page.locator("table tr").all()
                for row in comm_rows:
                    cells = await row.locator("td").all()
                    if len(cells) >= 2:
                        c_code = (await cells[0].inner_text()).strip()
                        c_desc = (await cells[1].inner_text()).strip()
                        if re.match(r'^\d{3,5}$', c_code) and c_desc:
                            commodity_codes.append(CommodityCode(code=c_code, description=c_desc))
            except Exception:
                pass

            # Extract Attachments
            attachments = []
            try:
                att_links = await detail_page.locator("a[href*='download'], a[href*='file'], a[href*='.pdf'], a[href*='.doc'], table#attachments a").all()
                for a_link in att_links:
                    href = await a_link.get_attribute("href")
                    link_text = (await a_link.inner_text()).strip()
                    
                    parent_row = a_link.locator("xpath=ancestor::tr")
                    row_cells = await parent_row.locator("td").all() if await parent_row.count() > 0 else []
                    
                    file_name = None
                    if len(row_cells) >= 1:
                        cell_text = (await row_cells[0].inner_text()).strip()
                        if cell_text and cell_text.lower() != "download":
                            file_name = cell_text
                    
                    if not file_name:
                        file_name = link_text or "attachment"

                    if href:
                        full_download_url = urllib.parse.urljoin(self.BASE_URL, href)
                        file_type = file_name.split(".")[-1] if "." in file_name else None
                        attachments.append(
                            Attachment(
                                file_name=file_name,
                                download_url=full_download_url,
                                file_type=file_type
                            )
                        )
            except Exception:
                pass

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
                updated_at=now_str
            )
        except Exception as e:
            print(f"Error scraping detail page {url}: {e}")
            return None
        finally:
            await detail_page.close()
