import pytest
from src.scrapers.jaggaer import (
    IowaJaggaerScraper,
    DelawareJaggaerScraper,
    UtahJaggaerScraper,
    JaggaerLoginRequiredError,
    LoginWalledJaggaerScraper,
)
from src.storage import DeliveryRecord
from tests.conftest import local_html_server


IOWA_LIST_HTML = """
<html><body>
<table id="bids"><tr><td><a href="/Home/BidInfo?bidId=abc-123">EPD-24-001</a></td></tr></table>
</body></html>
"""

IOWA_DETAIL_HTML = """
<html><body>
<div class="row"><div class="col-3"><label>Bid Number</label></div><div class="col-9">EPD-24-001</div></div>
<div class="row"><div class="col-3"><label>Solicitation</label></div><div class="col-9">Road Salt Supply</div></div>
<div class="row"><div class="col-3"><label>Name</label></div><div class="col-9">Iowa DOT</div></div>
<div class="row"><div class="col-3"><label>From</label></div><div class="col-9">08/01/2026</div></div>
<div class="row"><div class="col-3"><label>Until</label></div><div class="col-9">09/01/2026</div></div>
<a href="/GetBidOpportunityDocument?id=1">Spec.pdf</a>
</body></html>
"""


@pytest.mark.asyncio
async def test_iowa_scrape_all_bids(crawler):
    scraper = IowaJaggaerScraper()
    with local_html_server(
        {lambda p: "BidInfo" in p: IOWA_DETAIL_HTML},
        default=IOWA_LIST_HTML,
    ) as base_url:
        scraper.base_url = base_url + "/"
        records = await scraper.scrape_all_bids(crawler, max_pages=1)

        assert len(records) == 1
        rec = records[0]
        assert isinstance(rec, DeliveryRecord)
        assert rec.source.source_opportunity_id == "EPD-24-001"
        assert rec.opportunity.title == "Road Salt Supply"
        assert rec.opportunity.agency_name == "Iowa DOT"
        assert rec.opportunity.published_at == "08/01/2026"
        assert rec.opportunity.due_at == "09/01/2026"
        assert len(rec.documents) == 1
        assert rec.documents[0].title == "Spec.pdf"


DELAWARE_LIST_HTML = """
<html><body>
<table><tr><td><a href="#" onclick="getBidDetail(555)">View</a></td></tr></table>
</body></html>
"""

DELAWARE_DETAIL_HTML = """
<html><body>
<h1>Bid Status Details</h1>
Bid Status Details for
Road Repair Contract
DOT-2026-01
Solicitation Ad Date
08/01/2026
Deadline for Bid Responses
08/25/2026
<a href="/files/spec.pdf">spec.pdf</a>
</body></html>
"""


@pytest.mark.asyncio
async def test_delaware_scrape_all_bids(crawler):
    scraper = DelawareJaggaerScraper()
    with local_html_server(
        {lambda p: "Details/555" in p: DELAWARE_DETAIL_HTML},
        default=DELAWARE_LIST_HTML,
    ) as base_url:
        scraper.base_url = base_url + "/"
        records = await scraper.scrape_all_bids(crawler, max_pages=1)

        assert len(records) == 1
        rec = records[0]
        assert rec.opportunity.title == "Road Repair Contract"
        assert rec.opportunity.solicitation_number == "DOT-2026-01"
        assert rec.opportunity.published_at == "08/01/2026"
        assert rec.opportunity.due_at == "08/25/2026"
        assert rec.opportunity.agency_name == "DOT"
        assert len(rec.documents) == 1


SOURCING_LIST_HTML = """
<html><body>
<table aria-label="Search Results">
<tbody>
<tr><td>
Open
<a class="btn-link-header" href="https://app01.jaggaer.com/apps/Router/ViewSourcingEvent?AuthToken=x">RFP for Widgets</a>
A great widget opportunity
Number

RFP-2026-77
Type

Request for Proposal
Open


08/01/2026 12:00 AM
Close


08/25/2026 11:59 PM
Contact

buyer@state.gov
<a id="BUTTON_PDF_VIEW_1" href="https://example.com/view.pdf">View as PDF</a>
</td></tr>
</tbody>
</table>
</body></html>
"""


@pytest.mark.asyncio
async def test_utah_sourcing_public_site_scrape(crawler):
    scraper = UtahJaggaerScraper()
    with local_html_server({}, default=SOURCING_LIST_HTML) as base_url:
        scraper.base_url = base_url
        records = await scraper.scrape_all_bids(crawler, run_id="test-run")

        assert len(records) == 1
        rec = records[0]
        assert rec.opportunity.title == "RFP for Widgets"
        assert rec.opportunity.status == "Open"
        assert rec.opportunity.solicitation_number == "RFP-2026-77"
        assert rec.source.access_level == "registration_required"
        assert len(rec.documents) == 1


@pytest.mark.asyncio
async def test_login_walled_scraper_raises():
    scraper = LoginWalledJaggaerScraper(
        state_code="ZZ", base_url="https://example.com", portal_name="Test Portal", reason="test"
    )
    with pytest.raises(JaggaerLoginRequiredError):
        await scraper.scrape_all_bids(crawler=None)
