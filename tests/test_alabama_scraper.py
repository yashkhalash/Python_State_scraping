import hashlib

import pytest
import src.scrapers.alabama as alabama_module
from src.scrapers.alabama import AlabamaScraper
from src.storage import DeliveryRecord
from tests.conftest import local_html_server


@pytest.fixture(autouse=True)
def _mock_object_storage(monkeypatch):
    """No test in this file should hit the network or write real files for
    document downloads/raw-capture storage - src/object_storage.py itself is
    exercised directly in tests/test_object_storage.py."""

    def fake_save_text(content, *, prefix, extension=".html"):
        sha256 = hashlib.sha256(content.encode("utf-8")).hexdigest()
        return f"file:///fake/{prefix}/{sha256}{extension}", sha256

    def fake_download_document(url, **kwargs):
        sha256 = hashlib.sha256(url.encode("utf-8")).hexdigest()
        return {
            "download_status": "downloaded",
            "storage_uri": f"file:///fake/documents/{sha256}.pdf",
            "sha256": sha256,
            "byte_size": 123,
            "mime_type": "application/pdf",
        }

    monkeypatch.setattr(alabama_module, "save_text", fake_save_text)
    monkeypatch.setattr(alabama_module, "download_document", fake_download_document)


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


LIST_PAGE_HTML = """
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

DETAIL_PAGE_HTML = """
<html>
  <body>
    <h1 class="solicitation-title">Road Resurfacing Project</h1>
    <p>Issuing Agency: Alabama Department of Transportation</p>
    <p>Publication Date: 08/01/2026</p>
    <p>Closing Date: 08/25/2026</p>
  </body>
</html>
"""


@pytest.mark.asyncio
async def test_scrape_all_bids_with_mock_page(crawler):
    scraper = AlabamaScraper()

    with local_html_server(
        {lambda p: "0000432654" in p: DETAIL_PAGE_HTML},
        default=LIST_PAGE_HTML,
    ) as base_url:
        scraper.BASE_URL = base_url + "/alabama"
        bids = await scraper.scrape_all_bids(crawler, max_pages=1)

        assert len(bids) == 1
        bid = bids[0]
        assert isinstance(bid, DeliveryRecord)
        assert bid.source.source_opportunity_id == "0000432654"
        assert bid.source.name == "Alabama Purchasing Group (BidNet Direct)"
        assert bid.opportunity.title == "Road Resurfacing Project"
        assert bid.opportunity.agency_name == "Alabama Department of Transportation"
        assert bid.opportunity.status == "Open"
        assert bid.opportunity.published_at == "08/01/2026"
        assert bid.opportunity.due_at == "08/25/2026"
        assert bid.documents == []
