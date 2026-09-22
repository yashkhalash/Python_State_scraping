import pytest
from playwright.async_api import async_playwright
from src.scrapers.ivalua import (
    IvaluaScraper,
    ArizonaIvaluaScraper,
    MarylandIvaluaScraper,
    OhioIvaluaScraper,
    VermontIvaluaScraper,
)
from src.storage import BidEvent


def test_ivalua_scraper_initialization():
    scraper = IvaluaScraper(state_code="AZ", base_url="https://app.az.gov", portal_name="Arizona Procurement Portal (APP)")
    assert scraper.state_code == "AZ"
    assert scraper.base_url == "https://app.az.gov"
    assert scraper.portal_name == "Arizona Procurement Portal (APP)"
    assert scraper.BROWSE_URL == "https://app.az.gov/page.aspx/en/rfp/request_browse_public"


def test_per_state_subclasses():
    az = ArizonaIvaluaScraper()
    md = MarylandIvaluaScraper()
    oh = OhioIvaluaScraper()
    vt = VermontIvaluaScraper()

    assert az.state_code == "AZ"
    assert az.base_url == "https://app.az.gov"

    assert md.state_code == "MD"
    assert md.base_url == "https://emma.maryland.gov"

    assert oh.state_code == "OH"
    assert oh.base_url == "https://ohiobuys.ohio.gov"

    assert vt.state_code == "VT"
    assert vt.base_url == "https://vtbuys.vermont.gov"

    for scraper in (az, md, oh, vt):
        assert scraper.BROWSE_URL.endswith("/page.aspx/en/rfp/request_browse_public")


def test_extract_event_id_standard_numeric():
    scraper = ArizonaIvaluaScraper()
    url = "https://app.az.gov/page.aspx/en/bpm/process_manage_extranet/13882"
    assert scraper._extract_event_id(url) == "13882"


def test_extract_event_id_fallback():
    scraper = ArizonaIvaluaScraper()
    url = "https://app.az.gov/page.aspx/en/bpm/process_manage_extranet/no-numeric"
    event_id = scraper._extract_event_id(url)
    assert event_id.startswith("AZ-")


GRID_HTML = """
<html>
  <body>
    <table id="body_x_grid_grd" class="ui very compact table iv-grid-view">
      <thead>
        <tr><th>Editing column</th><th>Code</th><th>Label</th><th>Publication begin date</th><th>Commodity</th><th>Agency</th><th>Status</th></tr>
      </thead>
      <tbody>
        <tr>
          <td><a href="/page.aspx/en/bpm/process_manage_extranet/13882">Edit</a></td>
          <td>BPM007709</td>
          <td>Request for Information - SNAP Outreach Program</td>
          <td>8/21/2026 9:00:00 AM</td>
          <td>Coordination</td>
          <td>Arizona Department of Economic Security</td>
          <td>Open for Bidding</td>
        </tr>
      </tbody>
    </table>
  </body>
</html>
"""

DETAIL_HTML = """
<html>
  <body>
    <h1>Request for Information - SNAP Outreach Program</h1>
    <table class="details-table">
      <tr><th>Label:</th><td>Request for Information - SNAP Outreach Program</td></tr>
      <tr><th>Agency:</th><td>Arizona Department of Economic Security</td></tr>
      <tr><th>Type:</th><td>Request for Information</td></tr>
      <tr><th>Status:</th><td>Open for Bidding</td></tr>
      <tr><th>Publication begin date:</th><td>08/21/2026</td></tr>
      <tr><th>Publication end date:</th><td>09/11/2026 3:00:59 PM</td></tr>
    </table>
    <div class="contact-info">
      <p>Contact Name: Jane Buyer</p>
      <p>Email: jane.buyer@az.gov</p>
      <p>Phone: 602-555-0123</p>
    </div>
    <table id="commodity-codes">
      <tr><td>91829</td><td>Social Services Coordination</td></tr>
    </table>
    <a href="/files/RFI_Document.pdf">RFI_Document.pdf</a>
  </body>
</html>
"""

BROWSER_CHECK_HTML = """
<html>
  <head><title>Browser check</title></head>
  <body>Checking your browser before granting access... reCAPTCHA</body>
</html>
"""


@pytest.mark.asyncio
async def test_scrape_all_bids_with_mock_page():
    scraper = ArizonaIvaluaScraper()

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context()

        async def handle_route(route):
            url = route.request.url
            if "process_manage_extranet" in url:
                await route.fulfill(status=200, body=DETAIL_HTML, content_type="text/html")
            else:
                await route.fulfill(status=200, body=GRID_HTML, content_type="text/html")

        await context.route("**/*", handle_route)

        bids = await scraper.scrape_all_bids(context, max_pages=1)
        await browser.close()

        assert len(bids) == 1
        bid = bids[0]
        assert isinstance(bid, BidEvent)
        assert bid.event_id == "BPM007709"
        assert bid.state == "AZ"
        assert bid.portal_name == "Arizona Procurement Portal (APP)"
        assert "SNAP Outreach" in bid.title
        assert bid.agency == "Arizona Department of Economic Security"
        assert bid.status == "Open for Bidding"
        assert bid.contact is not None
        assert bid.contact.name == "Jane Buyer"
        assert bid.contact.email == "jane.buyer@az.gov"
        assert len(bid.commodity_codes) == 1
        assert bid.commodity_codes[0].code == "91829"
        assert len(bid.attachments) == 1
        assert bid.attachments[0].file_name == "RFI_Document.pdf"


@pytest.mark.asyncio
async def test_scrape_falls_back_to_grid_data_when_detail_page_is_captcha_gated():
    scraper = OhioIvaluaScraper()

    async def handle_route(route):
        url = route.request.url
        if "process_manage_extranet" in url:
            await route.fulfill(status=200, body=BROWSER_CHECK_HTML, content_type="text/html")
        else:
            await route.fulfill(status=200, body=GRID_HTML, content_type="text/html")

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context()
        await context.route("**/*", handle_route)

        bids = await scraper.scrape_all_bids(context, max_pages=1)
        await browser.close()

        assert len(bids) == 1
        bid = bids[0]
        assert bid.state == "OH"
        assert bid.event_id == "BPM007709"
        assert "SNAP Outreach" in bid.title
        assert bid.agency == "Arizona Department of Economic Security"
        assert bid.contact is None
        assert bid.commodity_codes == []
        assert bid.attachments == []


@pytest.mark.asyncio
async def test_scrape_all_bids_defensive_parsing_no_grid_rows():
    scraper = MarylandIvaluaScraper()

    empty_html = "<html><body><p>No results</p></body></html>"

    async def handle_route(route):
        await route.fulfill(status=200, body=empty_html, content_type="text/html")

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context()
        await context.route("**/*", handle_route)

        bids = await scraper.scrape_all_bids(context, max_pages=1)
        await browser.close()

        assert bids == []
