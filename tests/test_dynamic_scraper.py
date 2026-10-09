"""
Tests for src/dynamic_scraper.py (selector-free Gemini extraction path).
No real network/API calls: Crawl4AI's fetch_page, the google-genai client,
and document downloads/raw-capture storage are all monkeypatched.
"""
import hashlib
from types import SimpleNamespace

import pytest

import src.dynamic_scraper as dynamic_scraper
from src.dynamic_scraper import (
    ExtractedDocument,
    ExtractedOpportunity,
    ExtractionResult,
    scrape_dynamic,
)
from src.storage import DeliveryRecord


@pytest.fixture(autouse=True)
def _mock_object_storage(monkeypatch):
    """No test in this file should hit the network or write real files -
    save_text/download_document are exercised directly in
    tests/test_object_storage.py instead."""

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

    monkeypatch.setattr(dynamic_scraper, "save_text", fake_save_text)
    monkeypatch.setattr(dynamic_scraper, "download_document", fake_download_document)
    async def fake_robots_allow(crawler, url):
        return True

    monkeypatch.setattr(dynamic_scraper, "robots_allow", fake_robots_allow)


def test_extract_opportunities_returns_empty_without_api_key(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    result = dynamic_scraper.extract_opportunities_from_page("some page text", page_url="https://x")
    assert result == []


def test_extract_opportunities_parses_client_response(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")

    expected = ExtractionResult(
        opportunities=[
            ExtractedOpportunity(title="Road Salt Supply", agency_name="Iowa DOT")
        ]
    )

    class FakeResponse:
        parsed = expected
        text = None

    class FakeModels:
        def generate_content(self, **kwargs):
            return FakeResponse()

    class FakeClient:
        models = FakeModels()

    monkeypatch.setattr(dynamic_scraper, "_get_client", lambda: FakeClient())

    result = dynamic_scraper.extract_opportunities_from_page("page text", page_url="https://x")
    assert len(result) == 1
    assert result[0].title == "Road Salt Supply"


def test_extract_opportunities_returns_empty_on_api_error(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")

    class FakeModels:
        def generate_content(self, **kwargs):
            raise RuntimeError("boom")

    class FakeClient:
        models = FakeModels()

    monkeypatch.setattr(dynamic_scraper, "_get_client", lambda: FakeClient())

    result = dynamic_scraper.extract_opportunities_from_page("page text", page_url="https://x")
    assert result == []


@pytest.mark.asyncio
async def test_scrape_dynamic_builds_valid_records(monkeypatch):
    async def fake_fetch_page(crawler, url, **kwargs):
        return SimpleNamespace(markdown="## Road Salt Supply\nIowa DOT", cleaned_html=None, html=None)

    monkeypatch.setattr(dynamic_scraper, "fetch_page", fake_fetch_page)

    extracted = [
        ExtractedOpportunity(
            title="Road Salt Supply",
            agency_name="Iowa DOT",
            solicitation_number="EPD-24-001",
            published_at_raw="August 1, 2026",
            due_at_raw="September 1, 2026",
            submission_url="https://portal.example/bid/1",
            documents=[ExtractedDocument(title="Spec.pdf", url="https://portal.example/spec.pdf")],
        )
    ]
    monkeypatch.setattr(dynamic_scraper, "extract_opportunities_from_page", lambda content, page_url: extracted)

    records = await scrape_dynamic(
        crawler=object(),
        list_url="https://portal.example/bids",
        state_code="IA",
        portal_name="Example Portal",
        base_url="https://portal.example",
        run_id="test-run",
    )

    assert len(records) == 1
    rec = records[0]
    assert isinstance(rec, DeliveryRecord)
    assert rec.opportunity.title == "Road Salt Supply"
    assert rec.opportunity.published_at == "2026-08-01"
    assert rec.opportunity.due_at == "2026-09-01"
    assert len(rec.documents) == 1
    assert rec.documents[0].download_status == "downloaded"
    assert rec.documents[0].storage_uri is not None
    assert rec.documents[0].sha256 is not None
    assert rec.crawl.record_status == "ok"
    assert rec.crawl.parser_version == "dynamic-gemini-v1"
    assert rec.raw_capture.storage_uri is not None


@pytest.mark.asyncio
async def test_scrape_dynamic_keeps_document_url_on_download_failure(monkeypatch):
    async def fake_fetch_page(crawler, url, **kwargs):
        return SimpleNamespace(markdown="listing page content", cleaned_html=None, html=None)

    monkeypatch.setattr(dynamic_scraper, "fetch_page", fake_fetch_page)
    monkeypatch.setattr(
        dynamic_scraper,
        "download_document",
        lambda url, **kwargs: {"download_status": "failed", "error": "connection refused"},
    )

    extracted = [
        ExtractedOpportunity(
            title="Road Salt Supply",
            documents=[ExtractedDocument(title="Spec.pdf", url="https://portal.example/spec.pdf")],
        )
    ]
    monkeypatch.setattr(dynamic_scraper, "extract_opportunities_from_page", lambda content, page_url: extracted)

    records = await scrape_dynamic(
        crawler=object(),
        list_url="https://portal.example/bids",
        state_code="IA",
        portal_name="Example Portal",
        base_url="https://portal.example",
        run_id="test-run",
    )

    assert len(records) == 1
    rec = records[0]
    # The document URL is kept even though the download failed - spec:
    # "The document URL must still be included."
    assert len(rec.documents) == 1
    assert rec.documents[0].url == "https://portal.example/spec.pdf"
    assert rec.documents[0].download_status == "failed"
    assert rec.crawl.record_status == "partial"
    assert any("document download failed" in e for e in rec.crawl.errors)


@pytest.mark.asyncio
async def test_scrape_dynamic_flags_incomplete_records_needs_review(monkeypatch):
    async def fake_fetch_page(crawler, url, **kwargs):
        return SimpleNamespace(markdown="some ambiguous listing", cleaned_html=None, html=None)

    monkeypatch.setattr(dynamic_scraper, "fetch_page", fake_fetch_page)

    extracted = [ExtractedOpportunity(title=None, due_at_raw="not a real date")]
    monkeypatch.setattr(dynamic_scraper, "extract_opportunities_from_page", lambda content, page_url: extracted)

    records = await scrape_dynamic(
        crawler=object(),
        list_url="https://portal.example/bids",
        state_code="XX",
        portal_name="Unknown Portal",
        base_url="https://portal.example",
        run_id="test-run",
    )

    assert len(records) == 1
    rec = records[0]
    assert rec.crawl.record_status == "partial"
    assert any("title not found" in e for e in rec.crawl.errors)
    assert any("could not parse due_at" in e for e in rec.crawl.errors)


@pytest.mark.asyncio
async def test_scrape_dynamic_follows_detail_page_and_merges_fields(monkeypatch):
    async def fake_fetch_page(crawler, url, **kwargs):
        if url == "https://portal.example/bids":
            return SimpleNamespace(markdown="listing page content", cleaned_html=None, html=None)
        elif url == "https://portal.example/bid/1":
            return SimpleNamespace(markdown="detail page content", cleaned_html=None, html=None)
        raise AssertionError(f"unexpected url {url}")

    monkeypatch.setattr(dynamic_scraper, "fetch_page", fake_fetch_page)

    listing_extracted = [
        ExtractedOpportunity(
            title="Road Salt Supply",
            agency_name="Iowa DOT",
            detail_page_url="https://portal.example/bid/1",
            documents=[ExtractedDocument(title="Listing doc", url="https://portal.example/listing.pdf")],
        )
    ]
    detail_extracted = [
        ExtractedOpportunity(
            title="Road Salt Supply (Full)",
            description="Full description only shown on the detail page.",
            due_at_raw="September 1, 2026",
            documents=[ExtractedDocument(title="Spec.pdf", url="https://portal.example/spec.pdf")],
        )
    ]

    def fake_extract(content, page_url):
        if page_url == "https://portal.example/bids":
            return listing_extracted
        elif page_url == "https://portal.example/bid/1":
            return detail_extracted
        raise AssertionError(f"unexpected page_url {page_url}")

    monkeypatch.setattr(dynamic_scraper, "extract_opportunities_from_page", fake_extract)

    records = await scrape_dynamic(
        crawler=object(),
        list_url="https://portal.example/bids",
        state_code="IA",
        portal_name="Example Portal",
        base_url="https://portal.example",
        run_id="test-run",
    )

    assert len(records) == 1
    rec = records[0]
    # Detail-page values win...
    assert rec.opportunity.title == "Road Salt Supply (Full)"
    assert rec.opportunity.description == "Full description only shown on the detail page."
    assert rec.opportunity.due_at == "2026-09-01"
    # ...but listing-only fields and documents from BOTH pages are kept.
    assert rec.opportunity.agency_name == "Iowa DOT"
    doc_urls = {d.url for d in rec.documents}
    assert doc_urls == {"https://portal.example/listing.pdf", "https://portal.example/spec.pdf"}
    # raw_capture reflects the detail page (the more authoritative source).
    assert rec.raw_capture.sha256 == hashlib.sha256(b"detail page content").hexdigest()
    assert rec.raw_capture.storage_uri.startswith("file:///fake/raw_captures/")


@pytest.mark.asyncio
async def test_scrape_dynamic_falls_back_when_detail_page_fetch_fails(monkeypatch):
    async def fake_fetch_page(crawler, url, **kwargs):
        if url == "https://portal.example/bids":
            return SimpleNamespace(markdown="listing page content", cleaned_html=None, html=None)
        raise RuntimeError("network error fetching detail page")

    monkeypatch.setattr(dynamic_scraper, "fetch_page", fake_fetch_page)

    listing_extracted = [
        ExtractedOpportunity(
            title="Road Salt Supply",
            detail_page_url="https://portal.example/bid/1",
        )
    ]
    monkeypatch.setattr(
        dynamic_scraper, "extract_opportunities_from_page", lambda content, page_url: listing_extracted
    )

    records = await scrape_dynamic(
        crawler=object(),
        list_url="https://portal.example/bids",
        state_code="IA",
        portal_name="Example Portal",
        base_url="https://portal.example",
        run_id="test-run",
    )

    assert len(records) == 1
    assert records[0].opportunity.title == "Road Salt Supply"


@pytest.mark.asyncio
async def test_scrape_dynamic_can_disable_detail_page_following(monkeypatch):
    calls = []

    async def fake_fetch_page(crawler, url, **kwargs):
        calls.append(url)
        return SimpleNamespace(markdown="listing page content", cleaned_html=None, html=None)

    monkeypatch.setattr(dynamic_scraper, "fetch_page", fake_fetch_page)

    listing_extracted = [
        ExtractedOpportunity(title="Road Salt Supply", detail_page_url="https://portal.example/bid/1")
    ]
    monkeypatch.setattr(
        dynamic_scraper, "extract_opportunities_from_page", lambda content, page_url: listing_extracted
    )

    await scrape_dynamic(
        crawler=object(),
        list_url="https://portal.example/bids",
        state_code="IA",
        portal_name="Example Portal",
        base_url="https://portal.example",
        run_id="test-run",
        follow_detail_pages=False,
    )

    assert calls == ["https://portal.example/bids"]


@pytest.mark.asyncio
async def test_scrape_dynamic_raises_when_list_url_disallowed_by_robots(monkeypatch):
    async def fake_robots_allow(crawler, url):
        return False

    monkeypatch.setattr(dynamic_scraper, "robots_allow", fake_robots_allow)

    async def fail_if_called(crawler, url, **kwargs):
        raise AssertionError("should not fetch a robots-disallowed URL")

    monkeypatch.setattr(dynamic_scraper, "fetch_page", fail_if_called)

    with pytest.raises(dynamic_scraper.RobotsDisallowedError):
        await scrape_dynamic(
            crawler=object(),
            list_url="https://portal.example/bids",
            state_code="XX",
            portal_name="Unknown Portal",
            base_url="https://portal.example",
            run_id="test-run",
        )


@pytest.mark.asyncio
async def test_scrape_dynamic_skips_detail_page_disallowed_by_robots(monkeypatch):
    async def fake_fetch_page(crawler, url, **kwargs):
        if url == "https://portal.example/bids":
            return SimpleNamespace(markdown="listing page content", cleaned_html=None, html=None)
        raise AssertionError(f"should not fetch disallowed detail page {url}")

    monkeypatch.setattr(dynamic_scraper, "fetch_page", fake_fetch_page)

    # Allow the listing page, disallow the detail page specifically.
    async def fake_robots_allow(crawler, url):
        return url != "https://portal.example/bid/1"

    monkeypatch.setattr(dynamic_scraper, "robots_allow", fake_robots_allow)

    listing_extracted = [
        ExtractedOpportunity(title="Road Salt Supply", detail_page_url="https://portal.example/bid/1")
    ]
    monkeypatch.setattr(
        dynamic_scraper, "extract_opportunities_from_page", lambda content, page_url: listing_extracted
    )

    records = await scrape_dynamic(
        crawler=object(),
        list_url="https://portal.example/bids",
        state_code="IA",
        portal_name="Example Portal",
        base_url="https://portal.example",
        run_id="test-run",
    )

    assert len(records) == 1
    assert records[0].opportunity.title == "Road Salt Supply"


@pytest.mark.asyncio
async def test_scrape_dynamic_returns_empty_for_blank_page(monkeypatch):
    async def fake_fetch_page(crawler, url, **kwargs):
        return SimpleNamespace(markdown="   ", cleaned_html=None, html=None)

    monkeypatch.setattr(dynamic_scraper, "fetch_page", fake_fetch_page)

    def fail_if_called(content, page_url):
        raise AssertionError("should not call extraction on blank page content")

    monkeypatch.setattr(dynamic_scraper, "extract_opportunities_from_page", fail_if_called)

    records = await scrape_dynamic(
        crawler=object(),
        list_url="https://portal.example/bids",
        state_code="XX",
        portal_name="Unknown Portal",
        base_url="https://portal.example",
        run_id="test-run",
    )
    assert records == []
