"""
Tests for src/convert_to_delivery_spec.py's bidevent_to_delivery_record,
which lets GA's legacy BidEvent output flow into the same
Postgres-persistence path as every other scraper. Document downloads are
mocked - no real network access.
"""
import src.convert_to_delivery_spec as convert_module
from src.convert_to_delivery_spec import bidevent_to_delivery_record
from src.storage import Attachment, BidContact, BidEvent, CommodityCode, DeliveryRecord


def _make_bid(**overrides):
    defaults = dict(
        event_id="ES-12345",
        state="GA",
        portal_name="Georgia Procurement Registry",
        title="Road Resurfacing",
        agency="Department of Transportation",
        solicitation_type="RFP",
        status="Open",
        post_date="2026-08-01",
        close_date="2026-08-25T14:00:00-05:00",
        detail_url="https://ssl.doas.state.ga.us/gpr/eventDetails?eventId=ES-12345",
        contact=BidContact(name="Jane Doe", email="jane@ga.gov"),
        commodity_codes=[CommodityCode(code="91829", description="Software Consulting")],
        attachments=[],
        scraped_at="2026-08-04T12:00:00Z",
        updated_at="2026-08-04T12:00:00Z",
    )
    defaults.update(overrides)
    return BidEvent(**defaults)


def test_bidevent_to_delivery_record_maps_core_fields():
    bid = _make_bid()
    record = bidevent_to_delivery_record(bid, run_id="test-run", parser_version="georgia-v1.0.0")

    assert isinstance(record, DeliveryRecord)
    assert record.source.name == "Georgia Procurement Registry"
    assert record.source.source_opportunity_id == "ES-12345"
    assert record.opportunity.title == "Road Resurfacing"
    assert record.opportunity.agency_name == "Department of Transportation"
    assert record.opportunity.categories == ["Software Consulting"]
    assert record.crawl.record_status == "ok"


def test_bidevent_to_delivery_record_downloads_attachments(monkeypatch):
    monkeypatch.setattr(
        convert_module,
        "download_document",
        lambda url, **kwargs: {
            "download_status": "downloaded",
            "storage_uri": "file:///fake/spec.pdf",
            "sha256": "abc123",
            "mime_type": "application/pdf",
        },
    )
    bid = _make_bid(attachments=[Attachment(file_name="Spec.pdf", download_url="https://example.com/spec.pdf")])

    record = bidevent_to_delivery_record(bid, run_id="test-run", parser_version="georgia-v1.0.0")

    assert len(record.documents) == 1
    assert record.documents[0].download_status == "downloaded"
    assert record.documents[0].storage_uri == "file:///fake/spec.pdf"
    assert record.crawl.record_status == "ok"


def test_bidevent_to_delivery_record_keeps_url_on_download_failure(monkeypatch):
    monkeypatch.setattr(
        convert_module, "download_document", lambda url, **kwargs: {"download_status": "failed", "error": "boom"}
    )
    bid = _make_bid(attachments=[Attachment(file_name="Spec.pdf", download_url="https://example.com/spec.pdf")])

    record = bidevent_to_delivery_record(bid, run_id="test-run", parser_version="georgia-v1.0.0")

    assert len(record.documents) == 1
    assert record.documents[0].url == "https://example.com/spec.pdf"
    assert record.documents[0].download_status == "failed"
    assert record.crawl.record_status == "partial"
    assert any("document download failed" in e for e in record.crawl.errors)


def test_bidevent_to_delivery_record_skips_download_when_disabled(monkeypatch):
    def fail_if_called(url, **kwargs):
        raise AssertionError("should not download when download_attachments=False")

    monkeypatch.setattr(convert_module, "download_document", fail_if_called)
    bid = _make_bid(attachments=[Attachment(file_name="Spec.pdf", download_url="https://example.com/spec.pdf")])

    record = bidevent_to_delivery_record(
        bid, run_id="test-run", parser_version="georgia-v1.0.0", download_attachments=False
    )

    assert record.documents[0].download_status == "link_only"
    assert record.crawl.record_status == "ok"
