"""
Convert current scraper output (data/output/*.json) into the delivery format
required by knowlege/scraper-team-minimum-spec (1).md.

Field mapping notes (spec field -> postgresql-data-dictionary.md table.column):
    source.name              -> procurement.source_system.name
    source.base_url          -> procurement.source_system.base_url
    source.opportunity_url   -> procurement.source_record.canonical_url
    source.source_opportunity_id -> procurement.source_record.external_id
    source.access_level      -> procurement.source_record.access_level
    opportunity.title            -> procurement.opportunity.canonical_title
    opportunity.agency_name      -> procurement.buying_organization.display_name
    opportunity.status           -> procurement.opportunity.status
    opportunity.notice_type      -> procurement.opportunity.opportunity_type
    opportunity.published_at     -> procurement.opportunity.published_at
    opportunity.due_at           -> procurement.opportunity.due_at
    opportunity.source_timezone  -> procurement.opportunity.source_timezone
    opportunity.categories       -> procurement.opportunity_tag -> taxonomy_term.label
    opportunity.submission_url   -> procurement.submission_requirement.submission_url
    opportunity.submission_method -> procurement.submission_requirement.method
    documents[]                  -> procurement.document / document_version
    documents[].sha256           -> procurement.document_version.content_hash
    documents[].storage_uri      -> procurement.document_version.storage_uri
    raw_capture                  -> procurement.raw_artifact
    raw_capture.sha256           -> procurement.raw_artifact.checksum_sha256
    raw_capture.storage_uri      -> procurement.raw_artifact.storage_uri
    crawl.run_id                 -> procurement.crawl_run.id
    crawl.parser_version         -> procurement.source_record_version.parser_version
    crawl.record_status          -> procurement.crawl_run.status

Fields the current scraper does not collect (raw page hash, per-document hash,
storage URIs) are emitted as null with an entry in crawl.errors, per the spec's
"missing values are null; do not fabricate to hide failures" rule.
"""
import json
import sys
from pathlib import Path
from datetime import datetime, timezone

INPUT_DIR = Path(__file__).resolve().parent.parent / "data" / "output"
OUTPUT_DIR = INPUT_DIR / "spec_compliant"

SOURCE_BASE_URLS = {
    "AL": "https://www.bidnetdirect.com/alabama",
    "GA": "https://ssl.doas.state.ga.us/gpr/",
}

TIMEZONE_BY_STATE = {
    "AL": "America/Chicago",
    "GA": "America/New_York",
}


def to_documents(attachments):
    docs = []
    for att in attachments or []:
        docs.append({
            "title": att.get("title") or att.get("name"),
            "url": att.get("url"),
            "document_type": att.get("document_type", "other"),
            "mime_type": att.get("mime_type"),
            "download_status": "link_only",
            "storage_uri": None,
            "sha256": None,
        })
    return docs


def convert_record(record, run_id, parser_version):
    state = record.get("state")
    errors = []

    if not record.get("attachments"):
        pass  # no documents observed; empty array is valid per spec

    errors.append("raw_capture not saved by current scraper; sha256/storage_uri unavailable")
    if record.get("attachments"):
        errors.append("document sha256/storage_uri not captured by current scraper")

    observed_at = record.get("scraped_at")
    if observed_at and not observed_at.endswith("Z"):
        try:
            dt = datetime.fromisoformat(observed_at)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            observed_at = dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
        except ValueError:
            pass

    contact = record.get("contact") or {}

    return {
        "schema_version": "1.0",
        "observed_at": observed_at,
        "source": {
            "name": record.get("portal_name"),
            "base_url": SOURCE_BASE_URLS.get(state),
            "opportunity_url": record.get("detail_url"),
            "source_opportunity_id": record.get("event_id"),
            "access_level": "public",
        },
        "opportunity": {
            "title": record.get("title"),
            "agency_name": record.get("agency"),
            "solicitation_number": None,
            "status": record.get("status"),
            "notice_type": record.get("solicitation_type"),
            "published_at": record.get("post_date"),
            "due_at": record.get("close_date"),
            "source_timezone": TIMEZONE_BY_STATE.get(state),
            "description": None,
            "categories": record.get("commodity_codes") or [],
            "submission_url": record.get("detail_url"),
            "submission_method": "external_portal",
        },
        "documents": to_documents(record.get("attachments")),
        "raw_capture": {
            "content_type": "text/html",
            "storage_uri": None,
            "sha256": None,
        },
        "crawl": {
            "run_id": run_id,
            "parser_version": parser_version,
            "record_status": "partial",
            "errors": errors,
        },
        "_contact_not_in_spec_schema": contact if contact else None,
    }


def convert_file(input_path: Path, run_id: str, parser_version: str, output_path: Path):
    records = json.loads(input_path.read_text())
    with output_path.open("w", encoding="utf-8") as out:
        for record in records:
            converted = convert_record(record, run_id, parser_version)
            out.write(json.dumps(converted, ensure_ascii=False) + "\n")
    print(f"Wrote {len(records)} records -> {output_path}")


def main():
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    run_id = f"team-run-{datetime.now(timezone.utc).strftime('%Y-%m-%d-%H%M%S')}"

    convert_file(
        INPUT_DIR / "alabama_bids.json",
        run_id=run_id,
        parser_version="alabama-v1.0.0",
        output_path=OUTPUT_DIR / "alabama_bids.jsonl",
    )
    convert_file(
        INPUT_DIR / "georgia_bids.json",
        run_id=run_id,
        parser_version="georgia-v1.0.0",
        output_path=OUTPUT_DIR / "georgia_bids.jsonl",
    )


if __name__ == "__main__":
    sys.exit(main())
