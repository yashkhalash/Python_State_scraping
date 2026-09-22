import pytest
from playwright.async_api import async_playwright
from src.scrapers.georgia import GeorgiaScraper
from src.storage import BidEvent

def test_georgia_scraper_initialization():
    scraper = GeorgiaScraper()
    assert scraper.BASE_URL == "https://ssl.doas.state.ga.us/gpr/"

@pytest.mark.asyncio
async def test_scrape_all_bids_with_mock_page():
    scraper = GeorgiaScraper()
    
    search_page_html = """
    <html>
      <body>
        <form action="/gpr/search" method="post">
          <input type="submit" value="Search" />
        </form>
        <table>
          <tr>
            <td><a href="eventDetails?eventId=2614210001">2614210001</a></td>
            <td>IT Consulting Services</td>
          </tr>
        </table>
      </body>
    </html>
    """

    detail_page_html = """
    <html>
      <body>
        <h1>Event Details: 2614210001</h1>
        <table class="event-info">
          <tr><th>Title:</th><td>IT Consulting Services</td></tr>
          <tr><th>Agency:</th><td>Department of Administrative Services</td></tr>
          <tr><th>Solicitation Type:</th><td>Request for Proposals</td></tr>
          <tr><th>Status:</th><td>Open</td></tr>
          <tr><th>Post Date:</th><td>08/01/2026</td></tr>
          <tr><th>Close Date:</th><td>08/30/2026 02:00 PM EST</td></tr>
        </table>
        <div class="contact-info">
          <p>Contact: Jane Smith</p>
          <p>Email: jsmith@doas.ga.gov</p>
          <p>Phone: 404-555-0199</p>
        </div>
        <table id="commodity-codes">
          <tr><th>Code</th><th>Description</th></tr>
          <tr><td>91829</td><td>Software Consulting</td></tr>
        </table>
        <table id="attachments">
          <tr><th>File</th><th>Action</th></tr>
          <tr><td>RFP_Document.pdf</td><td><a href="/gpr/files/RFP_Document.pdf">Download</a></td></tr>
        </table>
      </body>
    </html>
    """

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context()
        
        async def handle_route(route):
            url = route.request.url
            if "eventDetails" in url:
                await route.fulfill(status=200, body=detail_page_html, content_type="text/html")
            else:
                await route.fulfill(status=200, body=search_page_html, content_type="text/html")

        await context.route("**/*", handle_route)
        
        bids = await scraper.scrape_all_bids(context, max_pages=1)
        await browser.close()

        assert len(bids) == 1
        bid = bids[0]
        assert isinstance(bid, BidEvent)
        assert bid.event_id == "2614210001"
        assert bid.state == "GA"
        assert "IT Consulting" in bid.title
        assert bid.agency == "Department of Administrative Services"
        assert bid.contact is not None
        assert bid.contact.name == "Jane Smith"
        assert bid.contact.email == "jsmith@doas.ga.gov"
        assert len(bid.commodity_codes) == 1
        assert bid.commodity_codes[0].code == "91829"
        assert len(bid.attachments) == 1
        assert bid.attachments[0].file_name == "RFP_Document.pdf"

@pytest.mark.asyncio
async def test_scrape_all_bids_defensive_parsing():
    scraper = GeorgiaScraper()

    # Search page with detail link lacking query param
    search_html = """
    <html>
      <body>
        <a href="eventDetails/custom-id-99">Custom Link</a>
      </body>
    </html>
    """

    # Minimal detail page with almost all optional fields missing
    detail_html = """
    <html>
      <body>
        <h3>Simple Procurement Notice</h3>
        <p>No table available here.</p>
      </body>
    </html>
    """

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context()

        async def handle_route(route):
            url = route.request.url
            if "eventDetails" in url:
                await route.fulfill(status=200, body=detail_html, content_type="text/html")
            else:
                await route.fulfill(status=200, body=search_html, content_type="text/html")

        await context.route("**/*", handle_route)

        bids = await scraper.scrape_all_bids(context, max_pages=1)
        await browser.close()

        assert len(bids) == 1
        bid = bids[0]
        assert bid.title == "Simple Procurement Notice"
        assert bid.agency == "State of Georgia"
        assert bid.status == "Open"
        assert bid.commodity_codes == []
        assert bid.attachments == []
