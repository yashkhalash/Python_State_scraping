import pytest
from playwright.async_api import async_playwright
from src.scrapers.alabama import AlabamaScraper
from src.storage import BidEvent

def test_alabama_scraper_initialization():
    scraper = AlabamaScraper()
    assert scraper.BASE_URL == "https://www.bidnetdirect.com/alabama"

def test_extract_event_id_standard_numeric():
    scraper = AlabamaScraper()
    url = "https://www.bidnetdirect.com/alabama/solicitations/open-bids/road-repair/0000432654?selectProjectLocation="
    event_id = scraper._extract_event_id(url)
    assert event_id == "0000432654"

def test_extract_event_id_short_path():
    scraper = AlabamaScraper()
    url = "https://www.bidnetdirect.com/alabama/0000432654?"
    event_id = scraper._extract_event_id(url)
    assert event_id == "0000432654"

def test_extract_event_id_fallback():
    scraper = AlabamaScraper()
    url = "https://www.bidnetdirect.com/alabama/solicitations/open-bids/no-numeric-id"
    event_id = scraper._extract_event_id(url)
    assert event_id.startswith("AL-")
    assert len(event_id) > 3

@pytest.mark.asyncio
async def test_scrape_all_bids_with_mock_page():
    scraper = AlabamaScraper()

    list_page_html = """
    <html>
      <body>
        <div class="solicitation-list">
          <a class="solicitation-link" href="/alabama/solicitations/open-bids/road-resurfacing-project/0000432654?query=1">
            Road Resurfacing Project
          </a>
        </div>
      </body>
    </html>
    """

    detail_page_html = """
    <html>
      <body>
        <h1 class="solicitation-title">Road Resurfacing Project</h1>
        <table class="details-table">
          <tr><th>Issuing Agency:</th><td>Alabama Department of Transportation</td></tr>
          <tr><th>Publication Date:</th><td>08/01/2026</td></tr>
          <tr><th>Closing Date:</th><td>08/25/2026</td></tr>
        </table>
      </body>
    </html>
    """

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context()

        async def handle_route(route):
            url = route.request.url
            if "0000432654" in url:
                await route.fulfill(status=200, body=detail_page_html, content_type="text/html")
            else:
                await route.fulfill(status=200, body=list_page_html, content_type="text/html")

        await context.route("**/*", handle_route)

        bids = await scraper.scrape_all_bids(context, max_pages=1)
        await browser.close()

        assert len(bids) == 1
        bid = bids[0]
        assert isinstance(bid, BidEvent)
        assert bid.event_id == "0000432654"
        assert bid.state == "AL"
        assert bid.portal_name == "Alabama Purchasing Group (BidNet Direct)"
        assert bid.title == "Road Resurfacing Project"
        assert bid.agency == "Alabama Department of Transportation"
        assert bid.solicitation_type == "Solicitation"
        assert bid.status == "Open"
        assert bid.post_date == "08/01/2026"
        assert bid.close_date == "08/25/2026"
        assert bid.contact is None
        assert bid.commodity_codes == []
        assert bid.attachments == []
