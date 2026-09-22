import pytest
from src.scrapers.georgia import GeorgiaScraper
from src.storage import BidEvent
from tests.conftest import local_html_server


def test_georgia_scraper_initialization():
    scraper = GeorgiaScraper()
    assert scraper.BASE_URL == "https://ssl.doas.state.ga.us/gpr/"


SEARCH_PAGE_HTML = """
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

DETAIL_PAGE_HTML = """
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


@pytest.mark.asyncio
async def test_scrape_all_bids_with_mock_page(crawler):
    scraper = GeorgiaScraper()

    with local_html_server(
        {lambda p: "eventDetails" in p: DETAIL_PAGE_HTML},
        default=SEARCH_PAGE_HTML,
    ) as base_url:
        scraper.BASE_URL = base_url + "/gpr/"
        bids = await scraper.scrape_all_bids(crawler, max_pages=1)

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


SEARCH_HTML_NO_QUERY = """
<html>
  <body>
    <a href="eventDetails/custom-id-99">Custom Link</a>
  </body>
</html>
"""

DETAIL_HTML_MINIMAL = """
<html>
  <body>
    <h3>Simple Procurement Notice</h3>
    <p>No table available here.</p>
  </body>
</html>
"""


@pytest.mark.asyncio
async def test_scrape_all_bids_defensive_parsing(crawler):
    scraper = GeorgiaScraper()

    with local_html_server(
        {lambda p: "eventDetails" in p: DETAIL_HTML_MINIMAL},
        default=SEARCH_HTML_NO_QUERY,
    ) as base_url:
        scraper.BASE_URL = base_url + "/gpr/"
        bids = await scraper.scrape_all_bids(crawler, max_pages=1)

        assert len(bids) == 1
        bid = bids[0]
        assert bid.title == "Simple Procurement Notice"
        assert bid.agency == "State of Georgia"
        assert bid.status == "Open"
        assert bid.commodity_codes == []
        assert bid.attachments == []
