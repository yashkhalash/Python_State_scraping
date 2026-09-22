import json
import os
from pathlib import Path
from typing import List, Optional, Union
from pydantic import BaseModel, Field

class DeliverySource(BaseModel):
    name: str
    base_url: str
    opportunity_url: str
    source_opportunity_id: Optional[str] = None
    access_level: str = "public"

class DeliveryOpportunity(BaseModel):
    title: Optional[str] = None
    agency_name: Optional[str] = None
    solicitation_number: Optional[str] = None
    status: Optional[str] = None
    notice_type: Optional[str] = None
    published_at: Optional[str] = None
    due_at: Optional[str] = None
    source_timezone: Optional[str] = None
    description: Optional[str] = None
    categories: List[str] = Field(default_factory=list)
    submission_url: Optional[str] = None
    submission_method: str = "external_portal"

class DeliveryDocument(BaseModel):
    title: Optional[str] = None
    url: str
    document_type: str = "other"
    mime_type: Optional[str] = None
    download_status: str = "link_only"
    storage_uri: Optional[str] = None
    sha256: Optional[str] = None

class RawCapture(BaseModel):
    content_type: str = "text/html"
    storage_uri: Optional[str] = None
    sha256: Optional[str] = None

class CrawlInfo(BaseModel):
    run_id: str
    parser_version: str
    record_status: str = "ok"
    errors: List[str] = Field(default_factory=list)

class DeliveryRecord(BaseModel):
    """Matches the required output in knowlege/scraper-team-minimum-spec (1).md"""
    schema_version: str = "1.0"
    observed_at: str
    source: DeliverySource
    opportunity: DeliveryOpportunity
    documents: List[DeliveryDocument] = Field(default_factory=list)
    raw_capture: RawCapture
    crawl: CrawlInfo

class BidContact(BaseModel):
    name: Optional[str] = None
    email: Optional[str] = None
    phone: Optional[str] = None

class CommodityCode(BaseModel):
    code: str
    description: str

class Attachment(BaseModel):
    file_name: str
    download_url: str
    file_type: Optional[str] = None

class BidEvent(BaseModel):
    event_id: str
    state: str = "GA"
    portal_name: str = "Georgia Procurement Registry"
    title: str
    agency: str
    solicitation_type: str
    status: str
    post_date: str
    close_date: str
    detail_url: str
    contact: Optional[BidContact] = None
    commodity_codes: List[CommodityCode] = Field(default_factory=list)
    attachments: List[Attachment] = Field(default_factory=list)
    scraped_at: str
    updated_at: str

class StorageManager:
    def upsert_bids(self, filepath: Union[str, Path], new_bids: List[BidEvent]) -> None:
        filepath = Path(filepath)
        existing_bids = {}
        
        if filepath.exists():
            try:
                with open(filepath, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    for item in data:
                        bid = BidEvent(**item)
                        existing_bids[bid.event_id] = bid
            except Exception:
                existing_bids = {}

        for bid in new_bids:
            if bid.event_id in existing_bids:
                # Preserve original scraped_at
                orig_scraped_at = existing_bids[bid.event_id].scraped_at
                updated_bid = bid.model_copy(update={"scraped_at": orig_scraped_at})
                existing_bids[bid.event_id] = updated_bid
            else:
                existing_bids[bid.event_id] = bid

        os.makedirs(filepath.parent, exist_ok=True)
        tmp_file = filepath.with_name(f"{filepath.name}.tmp")
        
        with open(tmp_file, "w", encoding="utf-8") as f:
            json.dump([b.model_dump() for b in existing_bids.values()], f, indent=2)
            
        tmp_file.replace(filepath)

    def append_delivery_records(self, filepath: Union[str, Path], records: List[DeliveryRecord]) -> None:
        """Append spec-compliant records to a .json array file. Prior records are
        preserved (read, then re-written with the new ones appended), per the spec's
        'do not delete or replace previously delivered records' rule."""
        filepath = Path(filepath)
        existing: list = []

        if filepath.exists():
            try:
                with open(filepath, "r", encoding="utf-8") as f:
                    existing = json.load(f)
                if not isinstance(existing, list):
                    existing = []
            except Exception:
                existing = []

        existing.extend(record.model_dump() for record in records)

        os.makedirs(filepath.parent, exist_ok=True)
        tmp_file = filepath.with_name(f"{filepath.name}.tmp")

        with open(tmp_file, "w", encoding="utf-8") as f:
            json.dump(existing, f, indent=2, ensure_ascii=False)

        tmp_file.replace(filepath)
