import json
import pytest
from pathlib import Path
from src.storage import (
    StorageManager,
    BidEvent,
    BidContact,
    CommodityCode,
    Attachment,
    CrawlInfo,
    DeliveryOpportunity,
    DeliveryRecord,
    DeliverySource,
    RawCapture,
)

def test_bid_contact_model():
    contact = BidContact(name="Jane Doe", email="jane@ga.gov", phone="404-555-0100")
    assert contact.name == "Jane Doe"
    assert contact.email == "jane@ga.gov"
    assert contact.phone == "404-555-0100"

    # Test optional defaults
    empty_contact = BidContact()
    assert empty_contact.name is None
    assert empty_contact.email is None
    assert empty_contact.phone is None

def test_commodity_code_model():
    cc = CommodityCode(code="91829", description="Software Consulting")
    assert cc.code == "91829"
    assert cc.description == "Software Consulting"

def test_attachment_model():
    att = Attachment(file_name="RFP.pdf", download_url="https://example.com/rfp.pdf", file_type="pdf")
    assert att.file_name == "RFP.pdf"
    assert att.download_url == "https://example.com/rfp.pdf"
    assert att.file_type == "pdf"

    # Test optional file_type default
    att_no_type = Attachment(file_name="doc.txt", download_url="https://example.com/doc.txt")
    assert att_no_type.file_type is None

def test_bid_event_model():
    bid = BidEvent(
        event_id="ES-12345",
        state="GA",
        portal_name="Georgia Procurement Registry",
        title="Test Procurement",
        agency="Department of Administrative Services",
        solicitation_type="RFP",
        status="Open",
        post_date="2026-08-01",
        close_date="2026-08-25T14:00:00-05:00",
        detail_url="https://ssl.doas.state.ga.us/gpr/eventDetails?eventId=ES-12345",
        contact=BidContact(name="Jane Doe", email="jane@ga.gov", phone="404-555-0100"),
        commodity_codes=[CommodityCode(code="91829", description="Software Consulting")],
        attachments=[Attachment(file_name="RFP.pdf", download_url="https://example.com/rfp.pdf", file_type="pdf")],
        scraped_at="2026-08-04T12:00:00-05:00",
        updated_at="2026-08-04T12:00:00-05:00"
    )
    assert bid.event_id == "ES-12345"
    assert bid.state == "GA"
    assert bid.portal_name == "Georgia Procurement Registry"
    assert bid.contact.name == "Jane Doe"
    assert len(bid.commodity_codes) == 1
    assert bid.commodity_codes[0].code == "91829"
    assert len(bid.attachments) == 1

    # Test default values
    bid_defaults = BidEvent(
        event_id="ES-999",
        title="Minimal Bid",
        agency="DOAS",
        solicitation_type="ITB",
        status="Open",
        post_date="2026-08-01",
        close_date="2026-08-10",
        detail_url="https://ssl.doas.state.ga.us/gpr/eventDetails?eventId=ES-999",
        scraped_at="2026-08-04T12:00:00-05:00",
        updated_at="2026-08-04T12:00:00-05:00"
    )
    assert bid_defaults.state == "GA"
    assert bid_defaults.portal_name == "Georgia Procurement Registry"
    assert bid_defaults.contact is None
    assert bid_defaults.commodity_codes == []
    assert bid_defaults.attachments == []

def test_upsert_bids(tmp_path):
    output_file = tmp_path / "georgia_bids.json"
    storage = StorageManager()
    
    bid1 = BidEvent(
        event_id="ES-1",
        state="GA",
        portal_name="Georgia Procurement Registry",
        title="Original Title",
        agency="DOAS",
        solicitation_type="RFP",
        status="Open",
        post_date="2026-08-01",
        close_date="2026-08-20",
        detail_url="https://ssl.doas.state.ga.us/gpr/1",
        scraped_at="2026-08-01T10:00:00-05:00",
        updated_at="2026-08-01T10:00:00-05:00"
    )
    storage.upsert_bids(output_file, [bid1])
    
    assert output_file.exists()
    with open(output_file, "r", encoding="utf-8") as f:
        data = json.load(f)
    assert len(data) == 1
    assert data[0]["title"] == "Original Title"

    # Add bid2 and update bid1 title
    bid2 = BidEvent(
        event_id="ES-2",
        title="Second Bid",
        agency="GDOT",
        solicitation_type="ITB",
        status="Open",
        post_date="2026-08-02",
        close_date="2026-08-22",
        detail_url="https://ssl.doas.state.ga.us/gpr/2",
        scraped_at="2026-08-04T10:00:00-05:00",
        updated_at="2026-08-04T10:00:00-05:00"
    )
    bid1_updated = bid1.model_copy(update={"title": "Updated Title", "updated_at": "2026-08-04T10:00:00-05:00", "scraped_at": "2026-08-04T10:00:00-05:00"})
    
    storage.upsert_bids(output_file, [bid1_updated, bid2])
    
    with open(output_file, "r", encoding="utf-8") as f:
        data = json.load(f)
    
    assert len(data) == 2
    bids_by_id = {b["event_id"]: b for b in data}
    assert bids_by_id["ES-1"]["title"] == "Updated Title"
    # Verify scraped_at for existing bid ES-1 is preserved from original insert
    assert bids_by_id["ES-1"]["scraped_at"] == "2026-08-01T10:00:00-05:00"
    assert bids_by_id["ES-1"]["updated_at"] == "2026-08-04T10:00:00-05:00"
    assert bids_by_id["ES-2"]["scraped_at"] == "2026-08-04T10:00:00-05:00"


def _make_delivery_record(opportunity_url="https://portal.example/bid/1", title="Road Salt Supply"):
    return DeliveryRecord(
        observed_at="2026-08-01T12:00:00Z",
        source=DeliverySource(
            name="Example Portal",
            base_url="https://portal.example",
            opportunity_url=opportunity_url,
            source_opportunity_id="EPD-1",
            access_level="public",
        ),
        opportunity=DeliveryOpportunity(title=title),
        documents=[],
        raw_capture=RawCapture(content_type="text/html", sha256="abc123"),
        crawl=CrawlInfo(run_id="test-run", parser_version="test-v1"),
    )


def test_append_delivery_records_jsonl_writes_one_object_per_line(tmp_path):
    output_file = tmp_path / "bids.jsonl"
    storage = StorageManager()
    storage.append_delivery_records_jsonl(output_file, [_make_delivery_record()])

    lines = output_file.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    parsed = json.loads(lines[0])
    assert parsed["opportunity"]["title"] == "Road Salt Supply"


def test_append_delivery_records_jsonl_appends_without_touching_prior_lines(tmp_path):
    output_file = tmp_path / "bids.jsonl"
    storage = StorageManager()
    storage.append_delivery_records_jsonl(output_file, [_make_delivery_record(title="First")])
    storage.append_delivery_records_jsonl(output_file, [_make_delivery_record(title="Second")])

    lines = output_file.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    assert json.loads(lines[0])["opportunity"]["title"] == "First"
    assert json.loads(lines[1])["opportunity"]["title"] == "Second"
