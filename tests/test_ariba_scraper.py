import pytest
from src.scrapers.ariba import FloridaAribaScraper, NorthCarolinaAribaScraper
from src.storage import BidEvent
from tests.conftest import local_html_server


FL_LIST_HTML = """
<html><body>
<table>
<tr><td><a href="/search/bids/detail/12345">FL-12345</a></td></tr>
</table>
</body></html>
"""

FL_DETAIL_HTML = """
<html><body>
<h1 class="mat-headline">RFP for Widgets</h1><span>Department of Widgets</span>
<span class="mat-headline f-sm">Request for Proposal</span>
Advertisement Number: FL-12345
Advertisement Status: Open
Published Date/Time: 08/01/2026
End Date/Time: 08/25/2026
Name: Jane Buyer
Phone: 850-555-0100
jane.buyer@myflorida.com
<table><tr><td>91829</td><td>Widget Supplies</td></tr></table>
<table><tr><td><a class="document-link" href="/files/spec.pdf">spec.pdf</a></td></tr></table>
</body></html>
"""


@pytest.mark.asyncio
async def test_florida_scrape_all_bids(crawler):
    scraper = FloridaAribaScraper()
    with local_html_server(
        {lambda p: "detail/12345" in p: FL_DETAIL_HTML},
        default=FL_LIST_HTML,
    ) as base_url:
        scraper.base_url = base_url
        scraper.LIST_URL = base_url + "/search/bids"
        bids = await scraper.scrape_all_bids(crawler, max_pages=1)

        assert len(bids) == 1
        bid = bids[0]
        assert isinstance(bid, BidEvent)
        assert bid.state == "FL"
        assert bid.event_id == "FL-12345"
        assert bid.title == "RFP for Widgets"
        assert bid.agency == "Department of Widgets"
        assert bid.solicitation_type == "Request for Proposal"
        assert bid.status == "Open"
        assert bid.post_date == "08/01/2026"
        assert bid.close_date == "08/25/2026"
        assert bid.contact is not None
        assert bid.contact.name == "Jane Buyer"
        assert bid.contact.email == "jane.buyer@myflorida.com"
        assert len(bid.commodity_codes) == 1
        assert len(bid.attachments) == 1
        assert bid.attachments[0].file_name == "spec.pdf"


NC_LIST_HTML = """
<html><body>
<a class="details-link" href="/solicitations/details/999">Solicitation 999</a>
</body></html>
"""

NC_DETAIL_HTML = """
<html><body>
<h1>Road Salt Contract</h1>
<input id="evp_solicitationnbr" value="NC-2026-01" />
<input id="evp_department_name" value="NC DOT" />
<table>
<tr><th>Solicitation Type</th><td>IFB</td></tr>
<tr><th>Status</th><td>Open</td></tr>
<tr><th>Opening Date</th><td>08/01/2026</td></tr>
<tr><th>Posted Date</th><td>07/01/2026</td></tr>
</table>
</body></html>
"""


@pytest.mark.asyncio
async def test_north_carolina_scrape_all_bids(crawler):
    scraper = NorthCarolinaAribaScraper()
    with local_html_server(
        {lambda p: "details/999" in p: NC_DETAIL_HTML},
        default=NC_LIST_HTML,
    ) as base_url:
        scraper.base_url = base_url
        scraper.LIST_URL = base_url + "/solicitations/"
        bids = await scraper.scrape_all_bids(crawler, max_pages=1)

        assert len(bids) == 1
        bid = bids[0]
        assert bid.state == "NC"
        assert bid.event_id == "NC-2026-01"
        assert bid.title == "Road Salt Contract"
        assert bid.agency == "NC DOT"
        assert bid.solicitation_type == "IFB"
        assert bid.status == "Open"
        assert bid.post_date == "08/01/2026"
        assert bid.close_date == "07/01/2026"
